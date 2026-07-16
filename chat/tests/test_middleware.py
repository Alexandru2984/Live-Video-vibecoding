import re

from django.test import TestCase


class ContentSecurityPolicyTests(TestCase):
    def test_csp_header_present_with_nonce(self):
        resp = self.client.get('/')
        self.assertIn('Content-Security-Policy', resp)
        csp = resp['Content-Security-Policy']
        self.assertIn("default-src 'self'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertRegex(csp, r"script-src [^;]*'nonce-[\w-]+'")

    def test_nonce_in_header_matches_script_tags(self):
        resp = self.client.get('/')
        csp = resp['Content-Security-Policy']
        nonce = re.search(r"'nonce-([\w-]+)'", csp).group(1)
        html = resp.content.decode()
        script_nonces = re.findall(r'<script[^>]*nonce="([\w-]+)"', html)
        self.assertTrue(script_nonces)
        self.assertTrue(all(n == nonce for n in script_nonces))

    def test_nonce_changes_between_requests(self):
        a = re.search(r"'nonce-([\w-]+)'", self.client.get('/')['Content-Security-Policy']).group(1)
        b = re.search(r"'nonce-([\w-]+)'", self.client.get('/')['Content-Security-Policy']).group(1)
        self.assertNotEqual(a, b)

    def test_no_third_party_hosts_and_pinned_connect_src(self):
        csp = self.client.get('/')['Content-Security-Policy']
        self.assertNotIn('jsdelivr', csp)
        self.assertNotIn('https:', csp)  # no blanket https: exfiltration channel
        self.assertIn('wss://testserver', csp)
