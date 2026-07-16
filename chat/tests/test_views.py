import base64
import hashlib
import hmac

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from chat.models import ChatRoom, Message

User = get_user_model()

STRONG = 'Str0ng-Pass-9xQ!'


class RegistrationViewTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_get_renders(self):
        self.assertEqual(self.client.get('/register/').status_code, 200)

    def test_valid_registration_creates_user_and_logs_in(self):
        resp = self.client.post('/register/', {
            'username': 'newbie', 'email': '', 'password1': STRONG, 'password2': STRONG,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(User.objects.filter(username='newbie').exists())
        self.assertIn('_auth_user_id', self.client.session)

    def test_weak_password_rejected(self):
        resp = self.client.post('/register/', {
            'username': 'weakling', 'password1': '123', 'password2': '123',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username='weakling').exists())

    def test_password_mismatch_rejected(self):
        resp = self.client.post('/register/', {
            'username': 'mismatch', 'password1': STRONG, 'password2': STRONG + 'x',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username='mismatch').exists())

    def test_duplicate_email_rejected(self):
        User.objects.create_user('first', email='dup@example.com', password=STRONG)
        resp = self.client.post('/register/', {
            'username': 'second', 'email': 'dup@example.com', 'password1': STRONG, 'password2': STRONG,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username='second').exists())

    @override_settings(REGISTRATION_RATE_LIMIT=1)
    def test_rate_limited_per_ip(self):
        ok = self.client.post('/register/', {
            'username': 'one', 'password1': STRONG, 'password2': STRONG})
        self.assertEqual(ok.status_code, 302)
        self.client.logout()
        blocked = self.client.post('/register/', {
            'username': 'two', 'password1': STRONG, 'password2': STRONG})
        self.assertEqual(blocked.status_code, 200)
        self.assertFalse(User.objects.filter(username='two').exists())

    @override_settings(REGISTRATION_RATE_LIMIT=1)
    def test_rate_limit_ignores_spoofed_forwarded_for(self):
        ok = self.client.post('/register/', {
            'username': 'real', 'password1': STRONG, 'password2': STRONG})
        self.assertEqual(ok.status_code, 302)
        self.client.logout()
        # A forged left-most XFF entry must not mint a fresh rate-limit bucket.
        blocked = self.client.post('/register/', {
            'username': 'spoofer', 'password1': STRONG, 'password2': STRONG,
        }, HTTP_X_FORWARDED_FOR='6.6.6.6')
        self.assertEqual(blocked.status_code, 200)
        self.assertFalse(User.objects.filter(username='spoofer').exists())

    @override_settings(REGISTRATION_RATE_LIMIT=1)
    def test_rate_limit_keys_on_cloudflare_header(self):
        ok = self.client.post('/register/', {
            'username': 'first_ip', 'password1': STRONG, 'password2': STRONG,
        }, HTTP_CF_CONNECTING_IP='198.51.100.1')
        self.assertEqual(ok.status_code, 302)
        self.client.logout()
        # Same CF IP: blocked.
        blocked = self.client.post('/register/', {
            'username': 'same_ip', 'password1': STRONG, 'password2': STRONG,
        }, HTTP_CF_CONNECTING_IP='198.51.100.1')
        self.assertFalse(User.objects.filter(username='same_ip').exists())
        # Different CF IP: its own bucket.
        ok2 = self.client.post('/register/', {
            'username': 'other_ip', 'password1': STRONG, 'password2': STRONG,
        }, HTTP_CF_CONNECTING_IP='198.51.100.2')
        self.assertEqual(ok2.status_code, 302)

    @override_settings(ALLOW_REGISTRATION=False)
    def test_registration_disabled(self):
        self.assertEqual(self.client.get('/register/').status_code, 403)
        resp = self.client.post('/register/', {
            'username': 'nope', 'password1': STRONG, 'password2': STRONG})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(User.objects.filter(username='nope').exists())


@override_settings(LOGIN_RATE_LIMIT=3)
class LoginRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('victim', password=STRONG)

    def _fail(self, username='victim'):
        return self.client.post('/login/', {'username': username, 'password': 'wrong'})

    def test_failed_attempts_lock_out_even_correct_password(self):
        for _ in range(3):
            self._fail()
        resp = self.client.post('/login/', {'username': 'victim', 'password': STRONG})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, 'Prea multe încercări')

    def test_successful_login_not_counted(self):
        for _ in range(2):
            self._fail()
        resp = self.client.post('/login/', {'username': 'victim', 'password': STRONG})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)


class RoomMessagesViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('reader', password=STRONG)
        self.room = ChatRoom.objects.create(name='general')
        self.msgs = [
            Message.objects.create(room=self.room, user=self.user, content=f'm{i}')
            for i in range(35)
        ]

    def test_requires_login(self):
        resp = self.client.get('/room/general/messages/')
        self.assertEqual(resp.status_code, 302)

    def test_returns_recent_page_oldest_first(self):
        self.client.force_login(self.user)
        data = self.client.get('/room/general/messages/').json()
        self.assertEqual(len(data['messages']), 30)
        self.assertTrue(data['has_more'])
        ids = [m['id'] for m in data['messages']]
        self.assertEqual(ids, sorted(ids))  # oldest-first

    def test_pagination_before(self):
        self.client.force_login(self.user)
        first = self.client.get('/room/general/messages/').json()
        oldest = first['messages'][0]['id']
        older = self.client.get(f'/room/general/messages/?before={oldest}').json()
        self.assertEqual(len(older['messages']), 5)
        self.assertFalse(older['has_more'])
        self.assertTrue(all(m['id'] < oldest for m in older['messages']))

    def test_catchup_after(self):
        self.client.force_login(self.user)
        pivot = self.msgs[2].id  # 32 newer messages exist past this one
        page = self.client.get(f'/room/general/messages/?after={pivot}').json()
        self.assertEqual(len(page['messages']), 30)
        self.assertTrue(page['has_more'])
        ids = [m['id'] for m in page['messages']]
        self.assertEqual(ids, sorted(ids))  # oldest-first
        self.assertTrue(all(i > pivot for i in ids))

        rest = self.client.get(f'/room/general/messages/?after={ids[-1]}').json()
        self.assertEqual(len(rest['messages']), 2)
        self.assertFalse(rest['has_more'])

    def test_messages_include_iso_timestamp(self):
        self.client.force_login(self.user)
        data = self.client.get('/room/general/messages/').json()
        self.assertIn('iso', data['messages'][0])
        self.assertIn('T', data['messages'][0]['iso'])


class CreateRoomViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('maker', password=STRONG)

    def test_get_not_allowed(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/create/').status_code, 405)

    def test_requires_login(self):
        self.assertEqual(self.client.post('/create/', {'room_name': 'x'}).status_code, 302)

    def test_creates_room(self):
        self.client.force_login(self.user)
        data = self.client.post('/create/', {'room_name': 'lounge'}).json()
        self.assertTrue(data['success'])
        self.assertTrue(ChatRoom.objects.filter(name='lounge').exists())

    def test_rejects_invalid_name(self):
        self.client.force_login(self.user)
        data = self.client.post('/create/', {'room_name': 'bad name!'}).json()
        self.assertFalse(data['success'])
        self.assertFalse(ChatRoom.objects.filter(name='bad name!').exists())

    def test_rejects_duplicate(self):
        ChatRoom.objects.create(name='dup')
        self.client.force_login(self.user)
        data = self.client.post('/create/', {'room_name': 'dup'}).json()
        self.assertFalse(data['success'])

    @override_settings(ROOM_CREATION_RATE_LIMIT=2)
    def test_room_creation_rate_limited(self):
        cache.clear()
        self.client.force_login(self.user)
        for name in ('r1', 'r2'):
            self.assertTrue(self.client.post('/create/', {'room_name': name}).json()['success'])
        data = self.client.post('/create/', {'room_name': 'r3'}).json()
        self.assertFalse(data['success'])
        self.assertFalse(ChatRoom.objects.filter(name='r3').exists())


class IceServersViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('caller', password=STRONG)

    def test_requires_login(self):
        self.assertEqual(self.client.get('/ice-servers/').status_code, 302)

    def test_stun_only_by_default(self):
        self.client.force_login(self.user)
        data = self.client.get('/ice-servers/').json()
        self.assertTrue(data['iceServers'])
        self.assertFalse(any('username' in s for s in data['iceServers']))

    @override_settings(
        TURN_URLS=['turn:turn.example.com:3478'],
        TURN_SHARED_SECRET='top-secret',
        TURN_CREDENTIAL_TTL=3600,
    )
    def test_turn_credentials_are_valid_hmac(self):
        self.client.force_login(self.user)
        data = self.client.get('/ice-servers/').json()
        turn = next(s for s in data['iceServers'] if 'username' in s)
        expiry, _, username = turn['username'].partition(':')
        self.assertEqual(username, 'caller')
        expected = base64.b64encode(
            hmac.new(b'top-secret', turn['username'].encode(), hashlib.sha1).digest()
        ).decode()
        self.assertEqual(turn['credential'], expected)

    @override_settings(
        TURN_URLS=['turn:turn.example.com:3478'],
        TURN_SHARED_SECRET='top-secret',
        WEBRTC_FORCE_RELAY=True,
    )
    def test_force_relay_advertised_with_turn(self):
        self.client.force_login(self.user)
        data = self.client.get('/ice-servers/').json()
        self.assertEqual(data.get('iceTransportPolicy'), 'relay')

    @override_settings(WEBRTC_FORCE_RELAY=True)
    def test_force_relay_ignored_without_turn(self):
        self.client.force_login(self.user)
        data = self.client.get('/ice-servers/').json()
        self.assertNotIn('iceTransportPolicy', data)
