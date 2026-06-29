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

    @override_settings(ALLOW_REGISTRATION=False)
    def test_registration_disabled(self):
        self.assertEqual(self.client.get('/register/').status_code, 403)
        resp = self.client.post('/register/', {
            'username': 'nope', 'password1': STRONG, 'password2': STRONG})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(User.objects.filter(username='nope').exists())


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
