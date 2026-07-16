import asyncio

from asgiref.sync import async_to_sync, sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TransactionTestCase

import chat.consumers
import chat.routing
from chat.consumers import MAX_MESSAGE_LENGTH, RATE_LIMIT_COUNT
from chat.models import ChatRoom, Message

User = get_user_model()


def application():
    return URLRouter(chat.routing.websocket_urlpatterns)


async def drain(comm, timeout=0.3):
    out = []
    while True:
        try:
            out.append(await asyncio.wait_for(comm.receive_json_from(), timeout=timeout))
        except Exception:
            break
    return out


class ChatConsumerTests(TransactionTestCase):
    def setUp(self):
        # Presence is a module-level registry; isolate it between tests.
        chat.consumers._room_members.clear()
        self.room = ChatRoom.objects.create(name='general', description='x')
        self.ana = User.objects.create_user('ana', password='pw')
        self.john = User.objects.create_user('john', password='pw')

    async def _connect(self, user, room='general'):
        comm = WebsocketCommunicator(application(), f'/ws/chat/{room}/')
        comm.scope['user'] = user
        connected, code = await comm.connect()
        return comm, connected, code

    def test_anonymous_is_rejected(self):
        async def body():
            comm, connected, code = await self._connect(AnonymousUser())
            self.assertFalse(connected)
            self.assertEqual(code, 4401)
        async_to_sync(body)()

    def test_missing_room_is_rejected(self):
        async def body():
            comm, connected, code = await self._connect(self.ana, room='nope')
            self.assertFalse(connected)
            self.assertEqual(code, 4404)
        async_to_sync(body)()

    def test_rejected_connection_disconnects_cleanly(self):
        # A rejected handshake (room missing) must not crash in disconnect()
        # nor broadcast a spurious user_leave to the room.
        async def body():
            watcher, _, _ = await self._connect(self.john)
            await drain(watcher)
            comm, connected, _ = await self._connect(self.ana, room='nope')
            self.assertFalse(connected)
            await comm.disconnect()
            msgs = await drain(watcher)
            self.assertFalse(any(m.get('type') == 'user_leave' for m in msgs))
            await watcher.disconnect()
        async_to_sync(body)()

    def test_join_leave_announced_once_per_user_not_per_tab(self):
        async def body():
            watcher, _, _ = await self._connect(self.john)
            await drain(watcher)

            tab1, _, _ = await self._connect(self.ana)
            msgs = await drain(watcher)
            self.assertEqual(sum(m.get('type') == 'user_join' for m in msgs), 1)

            tab2, _, _ = await self._connect(self.ana)
            msgs = await drain(watcher)
            self.assertFalse(any(m.get('type') == 'user_join' for m in msgs))

            await tab2.disconnect()
            msgs = await drain(watcher)
            self.assertFalse(any(m.get('type') == 'user_leave' for m in msgs))

            await tab1.disconnect()
            msgs = await drain(watcher)
            self.assertEqual(sum(m.get('type') == 'user_leave' for m in msgs), 1)
            await watcher.disconnect()
        async_to_sync(body)()

    def test_authenticated_connect_gets_join_and_presence(self):
        async def body():
            comm, connected, _ = await self._connect(self.ana)
            self.assertTrue(connected)
            types = {m['type'] for m in await drain(comm)}
            self.assertIn('user_join', types)
            self.assertIn('presence', types)
            await comm.disconnect()
        async_to_sync(body)()

    def test_chat_message_is_broadcast_and_saved(self):
        async def body():
            comm, _, _ = await self._connect(self.ana)
            await drain(comm)
            await comm.send_json_to({'message': 'hello world'})
            msgs = await drain(comm)
            chat_msgs = [m for m in msgs if m.get('type') == 'message']
            self.assertEqual(len(chat_msgs), 1)
            self.assertEqual(chat_msgs[0]['message'], 'hello world')
            self.assertEqual(chat_msgs[0]['username'], 'ana')
            await comm.disconnect()
            count = await sync_to_async(Message.objects.filter(content='hello world').count)()
            self.assertEqual(count, 1)
        async_to_sync(body)()

    def test_message_length_cap(self):
        async def body():
            comm, _, _ = await self._connect(self.ana)
            await drain(comm)
            await comm.send_json_to({'message': 'x' * (MAX_MESSAGE_LENGTH + 1)})
            msgs = await drain(comm)
            self.assertTrue(any(m.get('type') == 'error' for m in msgs))
            await comm.disconnect()
            count = await sync_to_async(Message.objects.count)()
            self.assertEqual(count, 0)
        async_to_sync(body)()

    def test_rate_limiting(self):
        async def body():
            comm, _, _ = await self._connect(self.ana)
            await drain(comm)
            for i in range(RATE_LIMIT_COUNT + 3):
                await comm.send_json_to({'message': f'm{i}'})
            msgs = await drain(comm, timeout=0.5)
            self.assertTrue(any(m.get('type') == 'error' for m in msgs))
            saved = await sync_to_async(Message.objects.count)()
            self.assertEqual(saved, RATE_LIMIT_COUNT)
            await comm.disconnect()
        async_to_sync(body)()

    def test_typing_flood_is_throttled(self):
        async def body():
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            await drain(a); await drain(b)
            limit, _ = chat.consumers.RATE_LIMITS['typing']
            for _ in range(limit + 15):
                await a.send_json_to({'type': 'typing', 'is_typing': True})
            b_msgs = await drain(b, timeout=0.5)
            typing = [m for m in b_msgs if m.get('type') == 'typing']
            self.assertLessEqual(len(typing), limit)
            await a.disconnect(); await b.disconnect()
        async_to_sync(body)()

    def test_signal_flood_is_throttled(self):
        async def body():
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            await drain(a); await drain(b)
            limit, _ = chat.consumers.RATE_LIMITS['signal']
            for _ in range(limit + 20):
                await a.send_json_to({
                    'type': 'webrtc_signal', 'to': 'john', 'kind': 'candidate',
                    'payload': {'candidate': 'x'},
                })
            b_msgs = await drain(b, timeout=0.5)
            signals = [m for m in b_msgs if m.get('type') == 'webrtc_signal']
            self.assertLessEqual(len(signals), limit)
            await a.disconnect(); await b.disconnect()
        async_to_sync(body)()

    def test_webrtc_signal_is_targeted(self):
        async def body():
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            await drain(a); await drain(b)
            await a.send_json_to({
                'type': 'webrtc_signal', 'to': 'john', 'kind': 'offer',
                'payload': {'type': 'offer', 'sdp': 'x'},
            })
            a_msgs, b_msgs = await drain(a), await drain(b)
            b_sig = [m for m in b_msgs if m.get('type') == 'webrtc_signal']
            self.assertEqual(len(b_sig), 1)
            self.assertEqual(b_sig[0]['from_user'], 'ana')
            self.assertEqual(b_sig[0]['kind'], 'offer')
            self.assertFalse(any(m.get('type') == 'webrtc_signal' for m in a_msgs))
            await a.disconnect(); await b.disconnect()
        async_to_sync(body)()

    def test_call_join_broadcast_and_present_targeted(self):
        async def body():
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            await drain(a); await drain(b)

            await a.send_json_to({'type': 'call_join'})
            a_msgs, b_msgs = await drain(a), await drain(b)
            self.assertTrue(any(m.get('type') == 'call_join' and m.get('username') == 'ana' for m in b_msgs))
            self.assertFalse(any(m.get('type') == 'call_join' for m in a_msgs))

            await b.send_json_to({'type': 'call_present', 'to': 'ana'})
            a_msgs, b_msgs = await drain(a), await drain(b)
            self.assertTrue(any(m.get('type') == 'call_present' and m.get('username') == 'john' for m in a_msgs))
            self.assertFalse(any(m.get('type') == 'call_present' for m in b_msgs))
            await a.disconnect(); await b.disconnect()
        async_to_sync(body)()

    def test_typing_relayed_to_others_not_self(self):
        async def body():
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            await drain(a); await drain(b)
            await a.send_json_to({'type': 'typing', 'is_typing': True})
            a_msgs, b_msgs = await drain(a), await drain(b)
            self.assertTrue(any(m.get('type') == 'typing' and m.get('username') == 'ana' for m in b_msgs))
            self.assertFalse(any(m.get('type') == 'typing' for m in a_msgs))
            await a.disconnect(); await b.disconnect()
        async_to_sync(body)()
