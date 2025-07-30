#!/usr/bin/env python3
"""
Script pentru crearea unui utilizator nou
"""

import os
import sys
import django

def setup_django():
    """Configurează Django"""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'websocket_project.settings')
    django.setup()

def create_test_user():
    """Creează un utilizator de test"""
    from django.contrib.auth.models import User
    
    username = input("Introdu numele utilizatorului: ")
    email = input("Introdu email-ul (opțional): ") or f"{username}@example.com"
    password = input("Introdu parola: ") or "test123"
    
    try:
        if User.objects.filter(username=username).exists():
            print(f"❌ Utilizatorul '{username}' există deja!")
            return False
        
        user = User.objects.create_user(username=username, email=email, password=password)
        print(f"✅ Utilizatorul '{username}' a fost creat cu succes!")
        print(f"📋 Detalii:")
        print(f"   • Username: {username}")
        print(f"   • Email: {email}")
        print(f"   • Password: {password}")
        return True
        
    except Exception as e:
        print(f"❌ Eroare la crearea utilizatorului: {e}")
        return False

def main():
    print("👤 Crearea unui utilizator nou pentru chat...")
    print("=" * 45)
    
    setup_django()
    
    if create_test_user():
        print("\n🎉 Utilizatorul a fost creat!")
        print("Acum poți să te conectezi cu acest utilizator la:")
        print("http://localhost:8000/login/")
    else:
        print("\n❌ Nu s-a putut crea utilizatorul.")

if __name__ == '__main__':
    main()
