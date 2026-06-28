import secrets


class ContentSecurityPolicyMiddleware:
    """Attach a per-request CSP nonce and emit a Content-Security-Policy header.

    The nonce is exposed as ``request.csp_nonce`` (and via the context
    processor below) so inline <script> tags can opt in with
    ``nonce="{{ csp_nonce }}"``. Scripts without the nonce — e.g. an injected
    ``<script>`` from a stored-XSS attempt — are blocked by the browser.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)
        response = self.get_response(request)
        response.setdefault('Content-Security-Policy', self._policy(request.csp_nonce))
        return response

    @staticmethod
    def _policy(nonce):
        return '; '.join([
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net",
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            "font-src 'self' https://cdn.jsdelivr.net",
            "img-src 'self' data: blob:",
            "media-src 'self' blob: mediastream:",
            # wss: for the chat socket; stun:/turn: so WebRTC ICE is not blocked.
            "connect-src 'self' wss: https: stun: turn: turns:",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ])


def csp_nonce(request):
    """Template context processor exposing the current request's CSP nonce."""
    return {'csp_nonce': getattr(request, 'csp_nonce', '')}
