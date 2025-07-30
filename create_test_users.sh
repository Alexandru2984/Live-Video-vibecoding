#!/bin/bash

echo "👤 Crearea utilizatorilor de test..."
echo "=================================="

# Activează mediul virtual
source ../.venv/bin/activate

echo ""
echo "Crearea utilizatorului 'alice'..."
echo "
from django.contrib.auth.models import User
user, created = User.objects.get_or_create(
    username='alice',
    defaults={
        'email': 'alice@example.com',
        'first_name': 'Alice',
        'last_name': 'Johnson'
    }
)
if created:
    user.set_password('alice123')
    user.save()
    print('✅ Utilizatorul alice a fost creat - alice/alice123')
else:
    print('ℹ️  Utilizatorul alice există deja')
" | python manage.py shell

echo ""
echo "Crearea utilizatorului 'bob'..."
echo "
from django.contrib.auth.models import User
user, created = User.objects.get_or_create(
    username='bob',
    defaults={
        'email': 'bob@example.com',
        'first_name': 'Bob',
        'last_name': 'Smith'
    }
)
if created:
    user.set_password('bob123')
    user.save()
    print('✅ Utilizatorul bob a fost creat - bob/bob123')
else:
    print('ℹ️  Utilizatorul bob există deja')
" | python manage.py shell

echo ""
echo "Crearea utilizatorului 'charlie'..."
echo "
from django.contrib.auth.models import User
user, created = User.objects.get_or_create(
    username='charlie',
    defaults={
        'email': 'charlie@example.com',
        'first_name': 'Charlie',
        'last_name': 'Brown'
    }
)
if created:
    user.set_password('charlie123')
    user.save()
    print('✅ Utilizatorul charlie a fost creat - charlie/charlie123')
else:
    print('ℹ️  Utilizatorul charlie există deja')
" | python manage.py shell

echo ""
echo "=================================="
echo "🎉 Utilizatori de test creați!"
echo ""
echo "📋 Poți folosi aceste conturi pentru testare:"
echo "   • alice / alice123"
echo "   • bob / bob123" 
echo "   • charlie / charlie123"
echo "   • demo / demo123 (existent)"
echo "   • admin / admin123 (administrator)"
echo ""
echo "🚀 Pentru a testa comunicarea:"
echo "   1. Deschide două browsere/ferestre incognito"
echo "   2. Conectează-te cu utilizatori diferiți"
echo "   3. Intră în aceeași cameră de chat"
echo "   4. Începe să trimiți mesaje!"
