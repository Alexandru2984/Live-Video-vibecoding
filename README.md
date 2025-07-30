# Django WebSocket Chat Application

O aplicație modernă de chat în timp real construită cu Django și WebSockets folosind Django Channels.

## 🌟 Funcționalități

- **Chat în timp real** folosind WebSockets
- **Camere de chat multiple** cu descrieri personalizabile
- **Autentificare utilizatori** cu sistem de login/logout
- **Istoric mesaje** salvat în baza de date
- **Interfață modernă** cu Bootstrap 5
- **Notificări** pentru intrarea/ieșirea utilizatorilor
- **Design responsive** pentru desktop și mobile
- **Panel de administrare** Django integrat

## 🚀 Instalare și Configurare

### 1. Clonează sau descarcă proiectul
```bash
cd django_websocket_app
```

### 2. Creează un mediu virtual
```bash
python3 -m venv venv
source venv/bin/activate  # Pe Linux/Mac
# sau
venv\Scripts\activate     # Pe Windows
```

### 3. Instalează dependențele
```bash
pip install -r requirements.txt
```

### 4. Rulează scriptul de inițializare
```bash
python setup.py
```

### 5. Pornește serverul
```bash
python manage.py runserver
```

## 🔧 Utilizare

### Accesare Aplicație
- **Chat Principal**: http://localhost:8000/
- **Panel Admin**: http://localhost:8000/admin/

### Conturi Predefinite
- **Administrator**: 
  - Username: `admin`
  - Password: `admin123`
  
- **Utilizator Demo**: 
  - Username: `demo`
  - Password: `demo123`

### Funcționalități Chat

1. **Conectare**: Folosește unul din conturile de mai sus
2. **Creare cameră**: Click pe "Creează o Cameră Nouă"
3. **Intrare în cameră**: Click pe "Intră în cameră" pentru orice cameră
4. **Trimitere mesaje**: Scrie în câmpul de text și apasă Enter sau butonul "Trimite"
5. **Notificări**: Vezi când utilizatorii intră/ies din cameră

## 🏗️ Structura Proiectului

```
django_websocket_app/
├── websocket_project/          # Configurarea Django
│   ├── settings.py            # Setări aplicație
│   ├── urls.py               # URL-uri principale
│   ├── asgi.py               # Configurare ASGI pentru WebSockets
│   └── wsgi.py               # Configurare WSGI
├── chat/                      # Aplicația de chat
│   ├── models.py             # Modele (ChatRoom, Message)
│   ├── views.py              # View-uri Django
│   ├── consumers.py          # WebSocket consumers
│   ├── routing.py            # Rutare WebSocket
│   ├── urls.py               # URL-uri chat
│   ├── admin.py              # Configurare admin
│   └── templates/            # Template-uri HTML
├── manage.py                 # Script de management Django
├── setup.py                  # Script de inițializare
└── requirements.txt          # Dependențe Python
```

## 💻 Tehnologii Folosite

- **Django 4.2+** - Framework web Python
- **Django Channels** - Suport WebSocket pentru Django
- **Daphne** - Server ASGI pentru WebSockets
- **Bootstrap 5** - Framework CSS pentru interfață
- **SQLite** - Baza de date (poate fi schimbată)
- **Redis** (opțional) - Pentru scaling în producție

## 🔧 Configurare Avansată

### Folosirea Redis pentru Channel Layers (Producție)

1. Instalează Redis:
```bash
sudo apt-get install redis-server  # Ubuntu/Debian
brew install redis                 # macOS
```

2. Modifică în `settings.py`:
```python
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [('127.0.0.1', 6379)],
        },
    },
}
```

### Configurare pentru Producție

1. Setează `DEBUG = False` în `settings.py`
2. Configurează `ALLOWED_HOSTS`
3. Folosește o bază de date PostgreSQL
4. Configurează servicii statice cu nginx
5. Folosește Gunicorn + Daphne pentru serving

## 🐛 Depanare

### Probleme comune:

1. **WebSocket nu se conectează**:
   - Verifică că serverul rulează pe portul corect
   - Verifică firewall-ul pentru porturi

2. **Erori de import channels**:
   - Asigură-te că ai instalat toate dependențele
   - Activează mediul virtual

3. **Mesajele nu se salvează**:
   - Verifică că migrațiile au fost aplicate
   - Verifică permisiunile bazei de date

## 📝 API WebSocket

### Mesaje trimise către server:
```json
{
    "message": "Conținutul mesajului"
}
```

### Mesaje primite de la server:
```json
{
    "type": "message",
    "message": "Conținutul mesajului",
    "username": "nume_utilizator",
    "timestamp": "HH:MM"
}
```

```json
{
    "type": "user_join",
    "username": "nume_utilizator",
    "message": "nume_utilizator s-a alăturat conversației"
}
```

## 🤝 Contribuții

Contribuțiile sunt binevenite! Te rugăm să:

1. Faci fork la proiect
2. Creezi o ramură pentru feature-ul tău
3. Faci commit cu modificările
4. Trimiți un pull request

## 📄 Licență

Acest proiect este sub licența MIT. Vezi fișierul LICENSE pentru detalii.

## 📞 Suport

Pentru întrebări sau probleme, te rugăm să deschizi un issue în repository.

---

**Dezvoltat cu ❤️ folosind Django și WebSockets**
