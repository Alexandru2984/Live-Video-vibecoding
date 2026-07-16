# micutu chat — Django + WebSockets

Aplicație de chat în timp real (Django Channels) cu prezență live, indicator
„scrie…", istoric paginat cu catch-up la reconectare, camere private cu
invitații, notificări, temă light/dark responsive, PWA instalabilă și apel
video de grup (mesh WebRTC) cu partajare de ecran.

## ✨ Funcționalități

- Chat în timp real prin WebSockets (Django Channels + Daphne)
- Camere multiple, istoric salvat, „mesaje mai vechi" la scroll, separatoare pe zile
- Backfill automat al mesajelor pierdute la reconectare (dedupe pe id)
- Camere **private** cu link-uri de invitație semnate (expiră în 7 zile),
  proprietar, ștergere de cameră
- Ștergerea propriilor mesaje (hard delete, broadcast în cameră)
- Prezență live (cine e online) și indicator de tastare
- Înregistrare publică cu validare parolă + rate-limit pe IP
- Schimbare parolă și **ștergere de cont self-service** (mesajele se șterg în cascadă)
- Notificări sonore și desktop (opt-in), auto-reconnect WebSocket
- Temă light/dark, design responsive (mobile-first), **PWA instalabilă**
- Apel video **de grup** (mesh WebRTC, perfect negotiation, cap configurabil),
  partajare de ecran, wake lock pe mobil
- Panel de administrare Django

## 🔒 Securitate (implementat)

- `SECRET_KEY` obligatoriu în producție (fără fallback nesigur); `DEBUG=False` implicit
- Headere de securitate: CSP cu nonce (per request, **fără niciun host terț** —
  Bootstrap e self-hosted), HSTS, `X-Frame-Options`, `nosniff`, Referrer-Policy,
  cookii `HttpOnly`/`SameSite`/`Secure`
- WebSocket: autentificare obligatorie la conectare, membership check pentru
  camerele private, `AllowedHostsOriginValidator`, rate-limit pe **fiecare** tip
  de mesaj + limită de lungime/dimensiune
- Rate limiting HTTP: login (per IP + per user), înregistrare (per IP, cheiat pe
  `CF-Connecting-IP`/`X-Real-IP`, nu pe XFF falsificabil), creare de camere
- Semnalizare WebRTC țintită pe peer-id per conexiune, validat strict server-side
- Randare XSS-safe (fără `innerHTML` pe date de la utilizator)
- Identitatea WebRTC și textul notificărilor sunt generate server-side
- Comandă de rotire a parolelor: `python manage.py rotate_credentials`

> Notă de încredere a IP-ului: `CF-Connecting-IP`/`X-Real-IP` sunt de încredere
> doar dacă originea acceptă trafic exclusiv prin proxy — firewall-uiește
> porturile 80/443 la IP-urile Cloudflare (sau măcar nu expune originea direct).

## 🔐 Privacy

- Zero CDN-uri / trackere: toate asset-urile sunt servite de pe domeniul propriu
- Email opțional la înregistrare; ștergere de cont + mesaje self-service
- Retenție configurabilă: `python manage.py purge_messages --days 90` (cron)
- `WEBRTC_FORCE_RELAY=True` (cu TURN configurat) ascunde IP-urile participanților
  între ei (media trece exclusiv prin relay)
- STUN-ul implicit e Google — pentru privacy complet rulează coturn propriu și
  setează `STUN_URLS`/`TURN_URLS`
- Sentry (opțional) e configurat cu `send_default_pii=False`

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
| `LOGIN_RATE_LIMIT` | nu (implicit `10`) | Încercări eșuate de login / IP sau user / 10 min |
| `ROOM_CREATION_RATE_LIMIT` | nu (implicit `10`) | Camere create / utilizator / oră |
| `MAX_CALL_PARTICIPANTS` | nu (implicit `6`) | Mărimea maximă a apelului video (mesh) |
| `WEBRTC_FORCE_RELAY` | nu (implicit `False`) | Media doar prin TURN (privacy; cere TURN configurat) |
| `STUN_URLS` | nu | CSV STUN (implicit Google STUN) |
| `TURN_URLS` | nu | CSV TURN, ex: `turn:turn.example.com:3478,turns:turn.example.com:5349` |
| `TURN_SHARED_SECRET` | nu | Secretul partajat coturn (`static-auth-secret`) |
| `TURN_CREDENTIAL_TTL` | nu (implicit `86400`) | Durata de viață a credențialelor TURN efemere (sec) |
| `SENTRY_DSN` | nu | Error tracking opțional (`pip install sentry-sdk`) |

> ⚠️ În producție, fără `DJANGO_SECRET_KEY` aplicația **refuză să pornească**
> (intenționat — fail closed). Configurează `/etc/video.env` înainte de restart.

## 📹 Video (mesh) și TURN

Apelul video e **mesh N-la-N** (fiecare pereche are propriul `RTCPeerConnection`,
negociere „perfect negotiation", semnalizare pe peer-id per conexiune — merge
corect și cu același cont în mai multe taburi). Serverul impune un număr maxim
de participanți (`MAX_CALL_PARTICIPANTS`, implicit 6) pentru că fiecare
participant urcă N-1 stream-uri; peste ~6 oameni e nevoie de un SFU, nu de mesh.

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

## 🛠️ Operare

- **Health check:** `GET /healthz` → `{"status": "ok"}` (fără autentificare) —
  pune-l în uptime monitoring extern.
- **Backup DB:** `scripts/backup_db.sh` (API-ul de backup SQLite, consistent cu
  aplicația pornită; păstrează ultimele 14 arhive). Cron sugerat:
  `30 3 * * * /home/micu/Video/scripts/backup_db.sh`
- **Retenție mesaje:** `python manage.py purge_messages --days 90 [--room X] [--dry-run]`
- **Rotire parole:**

```bash
python manage.py rotate_credentials --staff          # toți admin/staff
python manage.py rotate_credentials admin ana john   # conturi anume
python manage.py rotate_credentials --all --length 24
```

- **Deploy după update:** `git pull && .venv/bin/pip install -r requirements.txt
  && .venv/bin/python manage.py migrate && .venv/bin/python manage.py
  collectstatic --noinput && sudo systemctl restart video`

## 🧪 Teste & CI

```bash
DJANGO_SECRET_KEY=dev DJANGO_DEBUG=False python manage.py test
ruff check .
```

CI (GitHub Actions): lint (ruff), audit de dependențe (pip-audit, non-blocking),
system check, verificare de migrații lipsă și suita completă pe Python 3.12 și
3.13 — fără Redis (channel layer in-memory la test).

## 🏗️ Structură

```
websocket_project/   # settings, asgi (ASGI + origin validator), urls
chat/                # models, views, consumers, routing, forms, middleware (CSP)
chat/management/commands/   # rotate_credentials, purge_messages
chat/static/         # vendor (Bootstrap self-hosted), PWA (manifest, sw, icons)
chat/templates/      # base (temă), index, room, register, login, account, 404/500
scripts/backup_db.sh # backup SQLite consistent + rotație
```

> Fișierele de deployment (nginx vhost, `video.service`, `/etc/video.env`) sunt
> ținute **în afara git-ului** (`.gitignore`).

## 📝 Protocol WebSocket (rezumat)

Server → client la conectare: `welcome` (`peer_id`-ul tău + `call_count`).

Client → server: `{ "message": "..." }`, `{ "type": "typing", ... }`,
`{ "type": "delete_message", "id": N }`, `{ "type": "call_join" }` /
`call_leave` / `{ "type": "call_present", "to": "<peer>" }` și
`{ "type": "webrtc_signal", "to": "<peer>", "kind": "offer|answer|candidate", "payload": … }`.

Server → client: `message` (cu `id` + `iso`), `message_deleted`, `user_join`,
`user_leave`, `presence`, `typing`, `error`, `room_deleted`, `call_joined`,
`call_denied`, `call_join`, `call_leave`, `call_present` și `webrtc_signal`
(cu `from_user` + `from_peer`). Toate identitățile sunt ștampilate server-side.
