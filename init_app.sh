#!/bin/bash

echo "🚀 Inițializarea aplicației Django WebSocket Chat..."
echo "=================================================="

# Activează mediul virtual
source ../.venv/bin/activate

echo ""
echo "📦 Crearea migrațiilor pentru aplicația chat..."
python manage.py makemigrations chat

echo ""
echo "📦 Aplicarea tuturor migrațiilor..."
python manage.py migrate

echo ""
echo "👤 Crearea superuser (admin/admin123)..."
echo "from django.contrib.auth.models import User; User.objects.filter(username='admin').exists() or User.objects.create_superuser('admin', 'admin@example.com', 'admin123')" | python manage.py shell

echo ""
echo "👤 Crearea utilizator demo (demo/demo123)..."
echo "from django.contrib.auth.models import User; User.objects.filter(username='demo').exists() or User.objects.create_user('demo', 'demo@example.com', 'demo123')" | python manage.py shell

echo ""
echo "🏠 Crearea camerelor de chat exemplu..."
echo "
from chat.models import ChatRoom
rooms = [
    ('general', 'Conversație generală pentru toți utilizatorii'),
    ('tech', 'Discuții despre tehnologie și programare'),
    ('random', 'Conversații aleatorii și off-topic'),
]
for name, desc in rooms:
    room, created = ChatRoom.objects.get_or_create(name=name, defaults={'description': desc})
    print(f'Camera {name} - {\"creată\" if created else \"există deja\"}')
" | python manage.py shell

echo ""
echo "=================================================="
echo "🎉 Inițializarea completă!"
echo ""
echo "📋 Informații importante:"
echo "   • Admin: admin/admin123"
echo "   • Demo user: demo/demo123"
echo "   • Admin panel: http://localhost:8000/admin/"
echo "   • Chat app: http://localhost:8000/"
echo ""
echo "🚀 Pentru a porni serverul rulează:"
echo "   python manage.py runserver"
