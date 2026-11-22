import base64
import hashlib
import hmac
import json
import re
import time

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib import messages as flash
from django.contrib.auth import login, logout, views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.core import signing
from django.core.cache import cache
from django.db import connections
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponse, JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .consumers import _presence_list
from .forms import RegistrationForm
from .models import ALLOWED_REACTIONS, ChatRoom

# How many messages a history page returns.
HISTORY_PAGE_SIZE = 30

# Failed-login window (seconds) for the per-IP/per-username counters.
LOGIN_RATE_WINDOW = 600

# Signed invite links for private rooms.
INVITE_SALT = 'chat.room-invite'
INVITE_MAX_AGE = 7 * 24 * 3600  # seconds

# Strict ASCII room name pattern (prevents Unicode homograph spoofing).
ROOM_NAME_RE = re.compile(r'^[A-Za-z0-9_]+$')


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


def _serialize_message(m):
    return {
        'id': m.id,
        'username': m.user.username,
        'message': m.content,
        'timestamp': timezone.localtime(m.timestamp).strftime('%H:%M'),
        'iso': timezone.localtime(m.timestamp).isoformat(),
        'edited': m.edited_at is not None,
        'edited_iso': timezone.localtime(m.edited_at).isoformat() if m.edited_at else None,
        'is_pinned': m.is_pinned,
        'reply_to': m.serialize_reply(),
        'reactions': m.serialize_reactions(),
    }


def index(request):
    """Pagina principală cu lista camerelor de chat"""
    rooms = ChatRoom.objects.all()
    if request.user.is_authenticated:
        rooms = rooms.filter(
            Q(is_private=False) | Q(owner=request.user) | Q(members=request.user)
        ).distinct()
    else:
        rooms = rooms.filter(is_private=False)
    room_list = list(
        rooms.select_related('owner')
        .annotate(
            message_count=Count('messages', distinct=True),
            last_activity=Max('messages__timestamp'),
        )
        .order_by('-created_at')
    )
    for r in room_list:
        r.online_users = _presence_list(f'chat_{r.name}')
        r.online_count = len(r.online_users)
    return render(request, 'chat/index.html', {'rooms': room_list})


@login_required
def room(request, room_name):
    """Pagina unei camere de chat specifice"""
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    if not chat_room.can_access(request.user):
        flash.error(request, 'Camera este privată. Ai nevoie de o invitație.')
        return redirect('chat:index')
    # Last page of messages, returned oldest-first for display.
    messages = list(
        chat_room.messages
        .select_related('user', 'reply_to__user')
        .prefetch_related('reactions__user')
        .order_by('-id')[:HISTORY_PAGE_SIZE]
    )
    messages.reverse()
    for m in messages:
        m.reactions_data = m.serialize_reactions()
        m.reactions_json = json.dumps(m.reactions_data)

    pinned_messages = list(
        chat_room.messages.filter(is_pinned=True)
        .select_related('user')
        .order_by('-id')[:15]
    )
    room_members = []
    if chat_room.is_private and chat_room.owner_id == request.user.id:
        room_members = list(
            chat_room.members.exclude(pk=request.user.pk).order_by('username')
        )

    return render(request, 'chat/room.html', {
        'room_name': room_name,
        'chat_room': chat_room,
        'messages': messages,
        'pinned_messages': pinned_messages,
        'room_members': room_members,
        'allowed_reactions': ALLOWED_REACTIONS,
    })


@login_required
def room_messages(request, room_name):
    """JSON message history.

    ?before=<id>: page of messages older than <id> (infinite scroll upwards).
    ?after=<id>:  messages newer than <id>, oldest-first (reconnect catch-up;
                  repeat with the newest received id while has_more is true).
    """
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    if not chat_room.can_access(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)
    qs = (
        chat_room.messages
        .select_related('user', 'reply_to__user')
        .prefetch_related('reactions__user')
    )

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

    data = [_serialize_message(m) for m in page]
    return JsonResponse({'messages': data, 'has_more': has_more})


@login_required
def room_search(request, room_name):
    """Search message history inside a room by content or author username."""
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    if not chat_room.can_access(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)
    query = (request.GET.get('q') or '').strip()
    if len(query) < 2 or len(query) > 100:
        return JsonResponse({'messages': []})
    matches = list(
        chat_room.messages
        .select_related('user', 'reply_to__user')
        .prefetch_related('reactions__user')
        .filter(Q(content__icontains=query) | Q(user__username__icontains=query))
        .order_by('-id')[:HISTORY_PAGE_SIZE]
    )
    return JsonResponse({'messages': [_serialize_message(m) for m in matches]})


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
    # Room names must match the WebSocket route (\w+) with strict ASCII letters,
    # digits, and underscore (at least one alphanumeric character).
    if not ROOM_NAME_RE.fullmatch(room_name) or not room_name.strip('_'):
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

    is_private = request.POST.get('is_private') in ('on', 'true', '1')
    new_room, created = ChatRoom.objects.get_or_create(
        name=room_name,
        defaults={
            'description': description[:1000],
            'owner': request.user,
            'is_private': is_private,
        },
    )
    if created:
        if is_private:
            new_room.members.add(request.user)
        _bump_rate(rate_key, 3600)
        return JsonResponse({'success': True, 'room_name': room_name})
    return JsonResponse({'success': False, 'error': 'Camera există deja'})


@login_required
@require_http_methods(['POST'])
def room_invite(request, room_name):
    """Owner-only: mint a signed invite link for a private room."""
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    if chat_room.owner_id != request.user.id:
        return JsonResponse(
            {'success': False, 'error': 'Doar proprietarul poate invita.'}, status=403)
    rate_key = f'room-invite-rl:{request.user.pk}'
    if cache.get(rate_key, 0) >= getattr(settings, 'ROOM_INVITE_RATE_LIMIT', 30):
        return JsonResponse(
            {'success': False, 'error': 'Ai generat prea multe invitații recent.'},
            status=429,
        )
    _bump_rate(rate_key, 3600)
    token = signing.dumps(
        {'room': chat_room.name, 'v': chat_room.invite_version},
        salt=INVITE_SALT,
    )
    link = request.build_absolute_uri(reverse('chat:room_join', args=[token]))
    return JsonResponse({'success': True, 'invite_url': link})


@login_required
def room_join(request, token):
    """Redeem an invite link: become a member of the private room."""
    try:
        payload = signing.loads(token, salt=INVITE_SALT, max_age=INVITE_MAX_AGE)
    except signing.BadSignature:
        flash.error(request, 'Linkul de invitație este invalid sau a expirat.')
        return redirect('chat:index')
    if isinstance(payload, dict):
        room_name = payload.get('room', '')
        token_version = payload.get('v', 1)
    else:
        room_name = str(payload)
        token_version = 1
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    if token_version != chat_room.invite_version:
        flash.error(request, 'Linkul de invitație a fost revocat de proprietar.')
        return redirect('chat:index')
    chat_room.members.add(request.user)
    flash.success(request, f'Ai fost adăugat în camera {chat_room.name}.')
    return redirect('chat:room', room_name=chat_room.name)


@login_required
@require_http_methods(['POST'])
def room_revoke_invites(request, room_name):
    """Owner-only: rotate invite_version so all existing invite tokens stop working."""
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    if chat_room.owner_id != request.user.id:
        return JsonResponse(
            {'success': False, 'error': 'Doar proprietarul poate revoca invitațiile.'},
            status=403,
        )
    chat_room.invite_version += 1
    chat_room.save(update_fields=['invite_version'])
    return JsonResponse({'success': True, 'invite_version': chat_room.invite_version})


@login_required
@require_http_methods(['POST'])
def room_kick_member(request, room_name):
    """Owner-only: remove a member from a private room and evict their live socket."""
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    if chat_room.owner_id != request.user.id:
        return JsonResponse(
            {'success': False, 'error': 'Doar proprietarul poate elimina membri.'},
            status=403,
        )
    username = (request.POST.get('username') or '').strip()
    if not username or username == request.user.username:
        return JsonResponse(
            {'success': False, 'error': 'Utilizator invalid pentru eliminare.'},
            status=400,
        )
    target = chat_room.members.filter(username=username).first()
    if target is None:
        return JsonResponse(
            {'success': False, 'error': 'Utilizatorul nu este membru al camerei.'},
            status=404,
        )
    chat_room.members.remove(target)
    async_to_sync(get_channel_layer().group_send)(
        f'chat_{chat_room.name}',
        {'type': 'member_kicked', 'username': target.username},
    )
    return JsonResponse({'success': True, 'username': target.username})


@login_required
@require_http_methods(['POST'])
def room_update(request, room_name):
    """Owner-only: update room description or privacy status."""
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    if chat_room.owner_id != request.user.id:
        return JsonResponse(
            {'success': False, 'error': 'Doar proprietarul poate modifica setările camerei.'},
            status=403,
        )
    if 'description' in request.POST:
        chat_room.description = request.POST.get('description', '').strip()[:1000]
    if 'is_private' in request.POST:
        chat_room.is_private = request.POST.get('is_private') in ('on', 'true', '1')
        if chat_room.is_private:
            chat_room.members.add(request.user)
    chat_room.save(update_fields=['description', 'is_private'])
    return JsonResponse({
        'success': True,
        'description': chat_room.description,
        'is_private': chat_room.is_private,
    })


@login_required
@require_http_methods(['POST'])
def room_delete(request, room_name):
    """Owner-only: delete the room (messages cascade) and evict live clients."""
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    if chat_room.owner_id != request.user.id:
        return JsonResponse(
            {'success': False, 'error': 'Doar proprietarul poate șterge camera.'}, status=403)
    # Tell connected clients first; their sockets survive until they navigate.
    async_to_sync(get_channel_layer().group_send)(
        f'chat_{chat_room.name}', {'type': 'room_deleted'},
    )
    chat_room.delete()
    return JsonResponse({'success': True})


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


def healthz(request):
    """Liveness/readiness probe for uptime monitoring (no auth, no cache)."""
    try:
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        return JsonResponse({'status': 'error'}, status=503)
    return JsonResponse({'status': 'ok'})


def service_worker(request):
    """Serve sw.js from the root path so its scope covers the whole site."""
    path = finders.find('sw.js')
    if not path:
        raise Http404
    with open(path, 'rb') as f:
        response = HttpResponse(f.read(), content_type='application/javascript')
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Service-Worker-Allowed'] = '/'
    return response


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

    def post(self, request, *args, **kwargs):
        self._rate_keys = [
            f'pwchange-rl:user:{request.user.pk}',
            f'pwchange-rl:ip:{_client_ip(request)}',
        ]
        if any(cache.get(k, 0) >= settings.LOGIN_RATE_LIMIT for k in self._rate_keys):
            form = self.get_form()
            form.add_error(None, 'Prea multe încercări eșuate. Așteaptă câteva minute.')
            self._rate_keys = []
            return super().form_invalid(form)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        for key in getattr(self, '_rate_keys', []):
            _bump_rate(key, LOGIN_RATE_WINDOW)
        return super().form_invalid(form)

    def form_valid(self, form):
        flash.success(self.request, 'Parola a fost schimbată.')
        return super().form_valid(form)


@login_required
def delete_account(request):
    """Self-service account deletion; requires the current password.

    Hard delete: the user's messages and private owned rooms cascade with the
    account, so the data actually leaves the database and private rooms never
    become orphaned without an owner.
    """
    error = None
    if request.method == 'POST':
        rate_keys = [
            f'delacc-rl:user:{request.user.pk}',
            f'delacc-rl:ip:{_client_ip(request)}',
        ]
        if any(cache.get(k, 0) >= settings.LOGIN_RATE_LIMIT for k in rate_keys):
            error = 'Prea multe încercări eșuate. Așteaptă câteva minute.'
        elif request.user.check_password(request.POST.get('password', '')):
            user = request.user
            private_rooms = list(
                ChatRoom.objects.filter(owner=user, is_private=True).values_list('name', flat=True)
            )
            channel_layer = get_channel_layer()
            for rname in private_rooms:
                async_to_sync(channel_layer.group_send)(
                    f'chat_{rname}', {'type': 'room_deleted'},
                )
            ChatRoom.objects.filter(owner=user, is_private=True).delete()
            logout(request)
            user.delete()
            flash.success(request, 'Contul și mesajele tale au fost șterse definitiv.')
            return redirect('chat:index')
        else:
            for k in rate_keys:
                _bump_rate(k, LOGIN_RATE_WINDOW)
            error = 'Parolă incorectă.'
    return render(request, 'registration/delete_account.html', {'error': error})
