#!/usr/bin/env python3
"""
Script pentru inițializarea aplicației Django WebSocket Chat
"""

import os
import sys
import django

def setup_django():
    """Configurează Django"""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'websocket_project.settings')
    django.setup()

def create_superuser():
    """Creează un superuser pentru admin"""
    from django.contrib.auth.models import User
    try:
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
            print("✅ Superuser creat: admin/admin123")
        else:
            print("ℹ️  Superuser există deja")
    except Exception as e:
        print(f"❌ Eroare la crearea superuser: {e}")

def create_demo_user():
    """Creează un utilizator demo"""
    from django.contrib.auth.models import User
    try:
        if not User.objects.filter(username='demo').exists():
            User.objects.create_user('demo', 'demo@example.com', 'demo123')
            print("✅ Utilizator demo creat: demo/demo123")
        else:
            print("ℹ️  Utilizatorul demo există deja")
    except Exception as e:
        print(f"❌ Eroare la crearea utilizatorului demo: {e}")

def create_sample_rooms():
    """Creează camere de chat exemplu"""
    from chat.models import ChatRoom
    
    sample_rooms = [
        ('general', 'Conversație generală pentru toți utilizatorii'),
        ('tech', 'Discuții despre tehnologie și programare'),
        ('random', 'Conversații aleatorii și off-topic'),
    ]
    
    for name, description in sample_rooms:
        try:
            room, created = ChatRoom.objects.get_or_create(
                name=name,
                defaults={'description': description}
            )
            if created:
                print(f"✅ Camera '{name}' a fost creată")
            else:
                print(f"ℹ️  Camera '{name}' există deja")
        except Exception as e:
            print(f"❌ Eroare la crearea camerei '{name}': {e}")

def main():
    """Funcția principală"""
    print("🚀 Inițializarea aplicației Django WebSocket Chat...")
    print("=" * 50)
    
    # Configurează Django
    setup_django()
    
    # Import-uri după configurarea Django
    from django.core.management import execute_from_command_line
    
    # Rulează migrațiile
    print("\n📦 Aplicarea migrațiilor...")
    try:
        execute_from_command_line(['manage.py', 'makemigrations'])
        execute_from_command_line(['manage.py', 'migrate'])
        print("✅ Migrațiile au fost aplicate cu succes")
    except Exception as e:
        print(f"❌ Eroare la aplicarea migrațiilor: {e}")
        return
    
    # Creează utilizatorii
    print("\n👤 Crearea utilizatorilor...")
    create_superuser()
    create_demo_user()
    
    # Creează camerele de chat exemplu
    print("\n🏠 Crearea camerelor de chat exemplu...")
    create_sample_rooms()
    
    print("\n" + "=" * 50)
    print("🎉 Inițializarea completă!")
    print("\n📋 Informații importante:")
    print("   • Admin: admin/admin123")
    print("   • Demo user: demo/demo123")
    print("   • Admin panel: http://localhost:8000/admin/")
    print("   • Chat app: http://localhost:8000/")
    print("\n🚀 Pentru a porni serverul rulează:")
    print("   python manage.py runserver")

if __name__ == '__main__':
    main()
