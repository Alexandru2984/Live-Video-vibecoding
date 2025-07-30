#!/usr/bin/env python3

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'websocket_project.settings')
django.setup()

from django.contrib.auth.models import User

# Creează utilizatori de test
users = [
    ('alice', 'alice123', 'alice@test.com'),
    ('bob', 'bob123', 'bob@test.com'),
    ('charlie', 'charlie123', 'charlie@test.com')
]

print("👤 Crearea utilizatorilor de test...")
print("=" * 40)

for username, password, email in users:
    user, created = User.objects.get_or_create(
        username=username,
        defaults={'email': email}
    )
    if created:
        user.set_password(password)
        user.save()
        print(f"✅ {username} / {password}")
    else:
        print(f"ℹ️  {username} există deja")

print("\n🎉 Utilizatori creați! Acum poți testa cu:")
print("   • alice / alice123")
print("   • bob / bob123") 
print("   • charlie / charlie123")
print("   • demo / demo123")
print("\n💡 Pentru testare:")
print("   1. Deschide două browsere/tab-uri")
print("   2. Conectează-te cu utilizatori diferiți")
print("   3. Intră în aceeași cameră")
print("   4. Trimite mesaje și vezi-le în timp real!")
