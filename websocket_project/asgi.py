import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "websocket_project.settings")

import django
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator

# Inițializează Django ÎNAINTE de a importa rute/consumers
django.setup()

django_asgi_app = get_asgi_application()

# Importuri care ating modele/config – abia după inițializare
import chat.routing  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # AllowedHostsOriginValidator rejects WebSocket handshakes whose Origin is
    # not in ALLOWED_HOSTS, blocking cross-site WebSocket hijacking (cookies are
    # sent on cross-origin WS connections, so SameSite alone is not enough).
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(chat.routing.websocket_urlpatterns)
        )
    ),
})
