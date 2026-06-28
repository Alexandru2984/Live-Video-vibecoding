#!/bin/bash
set -euo pipefail

# Initialise the Django WebSocket chat app.
# This script does NOT create any account with a hard-coded password.
# Create your admin interactively and rotate any existing weak passwords.

echo "🚀 Inițializarea aplicației Django WebSocket Chat..."
echo "=================================================="

# Activate the virtualenv if present.
if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
elif [ -f "../.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source ../.venv/bin/activate
fi

echo ""
echo "📦 Aplicarea migrațiilor..."
python manage.py migrate

echo ""
echo "🎨 Colectarea fișierelor statice..."
python manage.py collectstatic --noinput

echo ""
echo "🏠 Crearea camerelor de chat exemplu..."
python manage.py shell <<'PY'
from chat.models import ChatRoom
for name, desc in [
    ('general', 'Conversație generală pentru toți utilizatorii'),
    ('tech', 'Discuții despre tehnologie și programare'),
    ('random', 'Conversații aleatorii și off-topic'),
]:
    _, created = ChatRoom.objects.get_or_create(name=name, defaults={'description': desc})
    print(f"Camera {name} - {'creată' if created else 'există deja'}")
PY

echo ""
echo "=================================================="
echo "🎉 Inițializarea completă!"
echo ""
echo "👤 Creează un administrator (parolă la alegere, nu hard-codată):"
echo "   python manage.py createsuperuser"
echo ""
echo "🔑 Rotește parolele conturilor existente slabe:"
echo "   python manage.py rotate_credentials --staff"
echo ""
echo "🚀 Pornește serverul (dev):"
echo "   python manage.py runserver"
