from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy
from django.http import JsonResponse
from .models import ChatRoom, Message


def index(request):
    """Pagina principală cu lista camerelor de chat"""
    rooms = ChatRoom.objects.all().order_by('-created_at')
    return render(request, 'chat/index.html', {'rooms': rooms})


@login_required
def room(request, room_name):
    """Pagina unei camere de chat specifice"""
    chat_room = get_object_or_404(ChatRoom, name=room_name)
    messages = chat_room.messages.all().order_by('timestamp')[:50]  # Ultimele 50 de mesaje
    
    return render(request, 'chat/room.html', {
        'room_name': room_name,
        'chat_room': chat_room,
        'messages': messages
    })


@login_required
def create_room(request):
    """Creează o cameră nouă de chat"""
    if request.method == 'POST':
        room_name = request.POST.get('room_name', '').strip()
        description = request.POST.get('description', '').strip()
        
        if room_name:
            room, created = ChatRoom.objects.get_or_create(
                name=room_name,
                defaults={'description': description}
            )
            if created:
                return JsonResponse({'success': True, 'room_name': room_name})
            else:
                return JsonResponse({'success': False, 'error': 'Camera există deja'})
        else:
            return JsonResponse({'success': False, 'error': 'Numele camerei este obligatoriu'})
    
    return render(request, 'chat/create_room.html')


class CustomLoginView(auth_views.LoginView):
    template_name = 'registration/login.html'
    redirect_authenticated_user = True
    
    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy('chat:index')


class CustomLogoutView(auth_views.LogoutView):
    next_page = reverse_lazy('chat:index')
