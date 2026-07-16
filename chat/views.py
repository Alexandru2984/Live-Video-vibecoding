import base64
import hashlib
import hmac
import time

from django.conf import settings
from django.contrib import messages as flash
from django.contrib.auth import login, logout, views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .forms import RegistrationForm
from .models import ChatRoom, Message

# How many messages a history page returns.
HISTORY_PAGE_SIZE = 30

# Failed-login window (seconds) for the per-IP/per-username counters.
LOGIN_RATE_WINDOW = 600


def _client_ip(request):
    """Client IP behind Cloudflare/nginx.

    X-Forwarded-For is deliberately ignored: its left-most entry is attacker
    controlled (Cloudflare only appends), so keying rate limits on it lets
    anyone mint fresh identities per request. CF-Connecting-IP is set by
    Cloudflare; X-Real-IP is set by our nginx. For either to be trustworthy
    the origin must only accept proxied traffic (firewall to Cloudflare/nginx).
    """
    return (
        request.META.get('HTTP_CF_CONNECTING_IP')
        or request.META.get('HTTP_X_REAL_IP')
        or request.META.get('REMOTE_ADDR', '')
    ).strip()


def _bump_rate(key, window):
    """Atomically count one event; returns the count inside the window."""
    if cache.add(key, 1, window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # key expired between add() and incr()
        cache.add(key, 1, window)
        return 1


def index(request):
    """Pagina principală cu lista camerelor de chat"""
    rooms = ChatRoom.objects.all().order_by('-created_at')
    return render(request, 'chat/index.html', {'rooms': rooms})


@login_required
def room(request, room_name):
    """Pagina unei camere de chat specifice"""
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    # Last page of messages, returned oldest-first for display.
    messages = list(
        chat_room.messages.select_related('user').order_by('-id')[:HISTORY_PAGE_SIZE]
    )
    messages.reverse()

    return render(request, 'chat/room.html', {
        'room_name': room_name,
        'chat_room': chat_room,
        'messages': messages,
    })


@login_required
def room_messages(request, room_name):
    """JSON message history.

    ?before=<id>: page of messages older than <id> (infinite scroll upwards).
    ?after=<id>:  messages newer than <id>, oldest-first (reconnect catch-up;
                  repeat with the newest received id while has_more is true).
    """
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    qs = chat_room.messages.select_related('user')

    after = request.GET.get('after')
    before = request.GET.get('before')
    if after and after.isdigit():
        page = list(qs.filter(id__gt=int(after)).order_by('id')[:HISTORY_PAGE_SIZE])
        has_more = len(page) == HISTORY_PAGE_SIZE
    else:
        newest_first = qs.order_by('-id')
        if before and before.isdigit():
            newest_first = newest_first.filter(id__lt=int(before))
        page = list(newest_first[:HISTORY_PAGE_SIZE])
        has_more = len(page) == HISTORY_PAGE_SIZE
        page.reverse()

    data = [
        {
            'id': m.id,
            'username': m.user.username,
            'message': m.content,
            'timestamp': timezone.localtime(m.timestamp).strftime('%H:%M'),
            'iso': timezone.localtime(m.timestamp).isoformat(),
        }
        for m in page
    ]
    return JsonResponse({'messages': data, 'has_more': has_more})


@login_required
@require_http_methods(['POST'])
def create_room(request):
    """Creează o cameră nouă de chat (doar POST, JSON)."""
    room_name = request.POST.get('room_name', '').strip()
    description = request.POST.get('description', '').strip()

    if not room_name:
        return JsonResponse({'success': False, 'error': 'Numele camerei este obligatoriu'})
    if len(room_name) > 100:
        return JsonResponse({'success': False, 'error': 'Numele camerei este prea lung'})
    # Room names must match the WebSocket route (\w+): letters, digits, underscore.
    if not room_name.isidentifier() and not room_name.replace('_', '').isalnum():
        return JsonResponse({
            'success': False,
            'error': 'Folosește doar litere, cifre și underscore în numele camerei.',
        })

    rate_key = f'room-create-rl:{request.user.pk}'
    if cache.get(rate_key, 0) >= settings.ROOM_CREATION_RATE_LIMIT:
        return JsonResponse({
            'success': False,
            'error': 'Ai creat prea multe camere recent. Încearcă mai târziu.',
        })

    _, created = ChatRoom.objects.get_or_create(
        name=room_name,
        defaults={'description': description[:1000]},
    )
    if created:
        _bump_rate(rate_key, 3600)
        return JsonResponse({'success': True, 'room_name': room_name})
    return JsonResponse({'success': False, 'error': 'Camera există deja'})


def register(request):
    """Public self-registration with per-IP rate limiting."""
    if not settings.ALLOW_REGISTRATION:
        return HttpResponseForbidden('Înregistrarea este dezactivată.')
    if request.user.is_authenticated:
        return redirect('chat:index')

    rate_error = None
    if request.method == 'POST':
        ip = _client_ip(request)
        cache_key = f'registration-rl:{ip}'
        if cache.get(cache_key, 0) >= settings.REGISTRATION_RATE_LIMIT:
            rate_error = 'Prea multe înregistrări de la această adresă. Încearcă mai târziu.'
            form = RegistrationForm()
        else:
            form = RegistrationForm(request.POST)
            if form.is_valid():
                user = form.save()
                # Count only successful registrations against the limit.
                _bump_rate(cache_key, 3600)
                login(request, user)
                return redirect('chat:index')
    else:
        form = RegistrationForm()

    return render(request, 'registration/register.html', {
        'form': form,
        'rate_error': rate_error,
    })


@login_required
def ice_servers(request):
    """ICE servers for WebRTC. TURN credentials are short-lived HMAC tokens
    (coturn shared-secret mechanism) so we never ship a static password."""
    servers = []
    if settings.STUN_URLS:
        servers.append({'urls': settings.STUN_URLS})
    if settings.TURN_URLS and settings.TURN_SHARED_SECRET:
        expiry = int(time.time()) + settings.TURN_CREDENTIAL_TTL
        username = f'{expiry}:{request.user.username}'
        digest = hmac.new(
            settings.TURN_SHARED_SECRET.encode('utf-8'),
            username.encode('utf-8'),
            hashlib.sha1,
        ).digest()
        servers.append({
            'urls': settings.TURN_URLS,
            'username': username,
            'credential': base64.b64encode(digest).decode('ascii'),
        })
        if settings.WEBRTC_FORCE_RELAY:
            # Relay-only: peers never see each other's addresses.
            return JsonResponse({'iceServers': servers, 'iceTransportPolicy': 'relay'})
    return JsonResponse({'iceServers': servers})


class CustomLoginView(auth_views.LoginView):
    """Login with brute-force protection.

    Failed attempts are counted per client IP and per target username (so a
    distributed attack on one account is also slowed). Successful logins are
    never counted; a blocked attempt is not counted either.
    """

    template_name = 'registration/login.html'
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        username = (request.POST.get('username') or '').strip().lower()[:150]
        self._rate_keys = [
            f'login-rl:ip:{_client_ip(request)}',
            f'login-rl:user:{username}',
        ]
        if any(cache.get(k, 0) >= settings.LOGIN_RATE_LIMIT for k in self._rate_keys):
            form = self.get_form()
            form.add_error(None, 'Prea multe încercări eșuate. Așteaptă câteva minute.')
            self._rate_keys = []  # the blocked attempt itself is not counted
            return super().form_invalid(form)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        for key in getattr(self, '_rate_keys', []):
            _bump_rate(key, LOGIN_RATE_WINDOW)
        return super().form_invalid(form)

    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy('chat:index')


class CustomLogoutView(auth_views.LogoutView):
    next_page = reverse_lazy('chat:index')


class CustomPasswordChangeView(auth_views.PasswordChangeView):
    template_name = 'registration/password_change.html'
    success_url = reverse_lazy('chat:index')

    def form_valid(self, form):
        flash.success(self.request, 'Parola a fost schimbată.')
        return super().form_valid(form)


@login_required
def delete_account(request):
    """Self-service account deletion; requires the current password.

    Hard delete: the user's messages cascade with the account, so the data
    actually leaves the database (privacy by default).
    """
    error = None
    if request.method == 'POST':
        if request.user.check_password(request.POST.get('password', '')):
            user = request.user
            logout(request)
            user.delete()
            flash.success(request, 'Contul și mesajele tale au fost șterse definitiv.')
            return redirect('chat:index')
        error = 'Parolă incorectă.'
    return render(request, 'registration/delete_account.html', {'error': error})
