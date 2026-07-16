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
        response.setdefault(
            'Content-Security-Policy',
            self._policy(request.csp_nonce, request.get_host()),
        )
        return response

    @staticmethod
    def _policy(nonce, host):
        # Everything is self-hosted: no CDN hosts anywhere. A host source in
        # script-src would also bypass the nonce (any library on that CDN
        # becomes loadable by injected markup), so keep the list tight.
        return '; '.join([
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            "style-src 'self' 'unsafe-inline'",
            "font-src 'self'",
            "img-src 'self' data: blob:",
            "media-src 'self' blob: mediastream:",
            # Only our own socket endpoint; stun:/turn: so WebRTC ICE works.
            f"connect-src 'self' wss://{host} stun: turn: turns:",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ])


def csp_nonce(request):
    """Template context processor exposing the current request's CSP nonce."""
    return {'csp_nonce': getattr(request, 'csp_nonce', '')}
