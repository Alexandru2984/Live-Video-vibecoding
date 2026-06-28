# micutu chat — Django + WebSockets

Aplicație de chat în timp real (Django Channels) cu prezență live, indicator
„scrie…", istoric paginat, notificări, temă light/dark responsive și apel video
1:1 prin WebRTC.

## ✨ Funcționalități

- Chat în timp real prin WebSockets (Django Channels + Daphne)
- Camere multiple, cu istoric salvat și încărcare „mesaje mai vechi" la scroll
- Prezență live (cine e online) și indicator de tastare
- Înregistrare publică cu validare parolă + rate-limit pe IP
- Notificări sonore și desktop (opt-in), auto-reconnect WebSocket
- Temă light/dark, design responsive (mobile-first)
- Apel video 1:1 (WebRTC, perfect negotiation)
- Panel de administrare Django

## 🔒 Securitate (implementat)

- `SECRET_KEY` obligatoriu în producție (fără fallback nesigur); `DEBUG=False` implicit
- Headere de securitate: CSP cu nonce (per request), HSTS, `X-Frame-Options`,
  `nosniff`, Referrer-Policy, cookii `HttpOnly`/`SameSite`/`Secure`
- WebSocket: necesită autentificare la conectare, `AllowedHostsOriginValidator`
  (anti cross-site WebSocket hijacking), rate-limit + limită lungime mesaj
- Randare XSS-safe (fără `innerHTML` pe date de la utilizator)
- Identitatea WebRTC și textul notificărilor sunt generate server-side
- Comandă de rotire a parolelor: `python manage.py rotate_credentials`

## 🚀 Instalare (dev)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export DJANGO_DEBUG=True            # cheie ne-necesară în dev
python manage.py migrate
./init_app.sh                       # migrate + collectstatic + camere demo
python manage.py createsuperuser    # creează adminul tău (parola ta)
python manage.py runserver
```

## ⚙️ Variabile de mediu

Setate în producție prin systemd `EnvironmentFile=/etc/video.env`:

| Variabilă | Necesară | Descriere |
|-----------|----------|-----------|
| `DJANGO_SECRET_KEY` | **da** (prod) | Cheie secretă. Generează: `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `DJANGO_DEBUG` | nu (implicit `False`) | `True` doar local |
| `DJANGO_ALLOWED_HOSTS` | nu | CSV, ex: `video.micutu.com,localhost,127.0.0.1` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | nu | CSV cu schemă, ex: `https://video.micutu.com` |
| `REDIS_URL` | recomandat | Activează channel layer + cache pe Redis (ex: `redis://127.0.0.1:6379/0`) |
| `DJANGO_LOG_LEVEL` | nu | Implicit `INFO` |
| `ALLOW_REGISTRATION` | nu (implicit `True`) | Dezactivează signup-ul public cu `False` |
| `REGISTRATION_RATE_LIMIT` | nu (implicit `5`) | Înregistrări reușite / IP / oră |

> ⚠️ În producție, fără `DJANGO_SECRET_KEY` aplicația **refuză să pornească**
> (intenționat — fail closed). Configurează `/etc/video.env` înainte de restart.

## 🛡️ Rotirea parolelor

```bash
python manage.py rotate_credentials --staff          # toți admin/staff
python manage.py rotate_credentials admin ana john   # conturi anume
python manage.py rotate_credentials --all --length 24
```
Parolele generate sunt afișate o singură dată.

## 🏗️ Structură

```
websocket_project/   # settings, asgi (ASGI + origin validator), urls
chat/                # models, views, consumers, routing, forms, middleware (CSP)
chat/management/commands/rotate_credentials.py
chat/templates/      # base (temă), index, room, register, login
```

> Fișierele de deployment (nginx vhost, `video.service`, `/etc/video.env`) sunt
> ținute **în afara git-ului** (`.gitignore`).

## 📝 Protocol WebSocket (rezumat)

Client → server: `{ "message": "..." }`, `{ "type": "typing", "is_typing": true }`,
și mesaje WebRTC (`video_offer`/`video_answer`/`ice_candidate`/`video_call_request`/`video_call_end`).

Server → client: `message`, `user_join`, `user_leave`, `presence` (listă online),
`typing`, `error`, plus mesajele WebRTC de mai sus.
