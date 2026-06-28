from django.conf import settings
from django.contrib.auth import login, views as auth_views
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


def _client_ip(request):
    """Best-effort client IP behind nginx/Cloudflare (left-most XFF entry)."""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


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
        chat_room.messages.select_related('user').order_by('-timestamp')[:HISTORY_PAGE_SIZE]
    )
    messages.reverse()

    return render(request, 'chat/room.html', {
        'room_name': room_name,
        'chat_room': chat_room,
        'messages': messages,
    })


@login_required
def room_messages(request, room_name):
    """JSON history endpoint for infinite scroll: messages older than ?before=<id>."""
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    qs = chat_room.messages.select_related('user').order_by('-timestamp')

    before = request.GET.get('before')
    if before and before.isdigit():
        qs = qs.filter(id__lt=int(before))

    page = list(qs[:HISTORY_PAGE_SIZE])
    has_more = len(page) == HISTORY_PAGE_SIZE
    page.reverse()
    data = [
        {
            'id': m.id,
            'username': m.user.username,
            'message': m.content,
            'timestamp': timezone.localtime(m.timestamp).strftime('%H:%M'),
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

    _, created = ChatRoom.objects.get_or_create(
        name=room_name,
        defaults={'description': description[:1000]},
    )
    if created:
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
        attempts = cache.get(cache_key, 0)
        if attempts >= settings.REGISTRATION_RATE_LIMIT:
            rate_error = 'Prea multe înregistrări de la această adresă. Încearcă mai târziu.'
            form = RegistrationForm()
        else:
            form = RegistrationForm(request.POST)
            if form.is_valid():
                user = form.save()
                # Count only successful registrations against the limit.
                cache.set(cache_key, attempts + 1, 3600)
                login(request, user)
                return redirect('chat:index')
    else:
        form = RegistrationForm()

    return render(request, 'registration/register.html', {
        'form': form,
        'rate_error': rate_error,
    })


class CustomLoginView(auth_views.LoginView):
    template_name = 'registration/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy('chat:index')


class CustomLogoutView(auth_views.LogoutView):
    next_page = reverse_lazy('chat:index')
