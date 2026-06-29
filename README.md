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
| `STUN_URLS` | nu | CSV STUN (implicit Google STUN) |
| `TURN_URLS` | nu | CSV TURN, ex: `turn:turn.example.com:3478,turns:turn.example.com:5349` |
| `TURN_SHARED_SECRET` | nu | Secretul partajat coturn (`static-auth-secret`) |
| `TURN_CREDENTIAL_TTL` | nu (implicit `86400`) | Durata de viață a credențialelor TURN efemere (sec) |

> ⚠️ În producție, fără `DJANGO_SECRET_KEY` aplicația **refuză să pornească**
> (intenționat — fail closed). Configurează `/etc/video.env` înainte de restart.

## 📹 Video (mesh) și TURN

Apelul video e **mesh N-la-N** (fiecare pereche are propriul `RTCPeerConnection`,
negociere „perfect negotiation"). Pe majoritatea rețelelor STUN e suficient.

Endpoint-ul `/ice-servers/` livrează lista ICE; pentru TURN dă **credențiale
efemere HMAC** (mecanismul `static-auth-secret`/REST din coturn), deci nu se
expune nicio parolă statică. Ca să activezi TURN mai târziu:

1. Instalează `coturn`, setează în config-ul lui `use-auth-secret` +
   `static-auth-secret=<S>` și un realm; restricționează relay-ul cu
   `denied-peer-ip` pentru rețelele interne (10.x/192.168.x/169.254.x/127.x).
2. Pune în `/etc/video.env`: `TURN_SHARED_SECRET=<S>` și
   `TURN_URLS=turn:<host>:3478,turns:<host>:5349`.
3. Repornește serviciul. Clientul preia automat noile servere ICE.

> Notă: dacă domeniul e proxat prin Cloudflare, TURN nu poate trece prin proxy
> (e trafic non-HTTP); folosește un hostname DNS-only spre IP-ul origine.

## 🛡️ Rotirea parolelor

```bash
python manage.py rotate_credentials --staff          # toți admin/staff
python manage.py rotate_credentials admin ana john   # conturi anume
python manage.py rotate_credentials --all --length 24
```
Parolele generate sunt afișate o singură dată.

## 🧪 Teste

```bash
DJANGO_SECRET_KEY=dev DJANGO_DEBUG=False python manage.py test
```

Suita (consumer WebSocket, view-uri, middleware CSP, comanda de rotire) rulează
și în CI (GitHub Actions, `.github/workflows/ci.yml`) la fiecare push/PR, pe
Python 3.12 și 3.13, fără a necesita Redis (channel layer in-memory la test).

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
