import asyncio

from asgiref.sync import async_to_sync, sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TransactionTestCase, override_settings

import chat.consumers
import chat.routing
from chat.consumers import MAX_MESSAGE_LENGTH, RATE_LIMIT_COUNT
from chat.models import ChatRoom, Message

User = get_user_model()


def application():
    return URLRouter(chat.routing.websocket_urlpatterns)


async def drain(comm, timeout=0.06):
    out = []
    while True:
        try:
            out.append(await asyncio.wait_for(comm.receive_json_from(), timeout=timeout))
        except Exception:
            break
    return out


def peer_of(msgs):
    """Extract this connection's peer id from its drained welcome message."""
    return next(m['peer_id'] for m in msgs if m.get('type') == 'welcome')


class ChatConsumerTests(TransactionTestCase):
    def setUp(self):
        # Presence/call are module-level registries; isolate them between tests.
        chat.consumers._room_members.clear()
        chat.consumers._call_members.clear()
        chat.consumers._room_peers.clear()
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

    def test_private_room_rejects_non_members(self):
        async def body():
            room = await sync_to_async(ChatRoom.objects.create)(
                name='vault', is_private=True)
            await sync_to_async(room.members.add)(self.ana)

            comm, connected, code = await self._connect(self.john, room='vault')
            self.assertFalse(connected)
            self.assertEqual(code, 4403)

            member, connected, _ = await self._connect(self.ana, room='vault')
            self.assertTrue(connected)
            await member.disconnect()
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
            self.assertIsInstance(chat_msgs[0]['id'], int)
            self.assertIn('T', chat_msgs[0]['iso'])
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
            await drain(a)
            b_peer = peer_of(await drain(b))
            limit, _ = chat.consumers.RATE_LIMITS['signal']
            for _ in range(limit + 20):
                await a.send_json_to({
                    'type': 'webrtc_signal', 'to': b_peer, 'kind': 'candidate',
                    'payload': {'candidate': 'x'},
                })
            b_msgs = await drain(b, timeout=0.5)
            signals = [m for m in b_msgs if m.get('type') == 'webrtc_signal']
            self.assertLessEqual(len(signals), limit)
            self.assertGreater(len(signals), 0)
            await a.disconnect(); await b.disconnect()
        async_to_sync(body)()

    def test_welcome_carries_unique_peer_ids_per_connection(self):
        async def body():
            tab1, _, _ = await self._connect(self.ana)
            tab2, _, _ = await self._connect(self.ana)
            p1, p2 = peer_of(await drain(tab1)), peer_of(await drain(tab2))
            self.assertNotEqual(p1, p2)

            # Signalling to one tab's peer id must not reach the other tab.
            b, _, _ = await self._connect(self.john)
            await drain(b)
            await b.send_json_to({
                'type': 'webrtc_signal', 'to': p1, 'kind': 'offer',
                'payload': {'type': 'offer', 'sdp': 'x'},
            })
            t1_msgs, t2_msgs = await drain(tab1), await drain(tab2)
            self.assertTrue(any(m.get('type') == 'webrtc_signal' for m in t1_msgs))
            self.assertFalse(any(m.get('type') == 'webrtc_signal' for m in t2_msgs))
            await tab1.disconnect(); await tab2.disconnect(); await b.disconnect()
        async_to_sync(body)()

    def test_webrtc_signal_is_targeted(self):
        async def body():
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            a_peer = peer_of(await drain(a))
            b_peer = peer_of(await drain(b))
            await a.send_json_to({
                'type': 'webrtc_signal', 'to': b_peer, 'kind': 'offer',
                'payload': {'type': 'offer', 'sdp': 'x'},
            })
            a_msgs, b_msgs = await drain(a), await drain(b)
            b_sig = [m for m in b_msgs if m.get('type') == 'webrtc_signal']
            self.assertEqual(len(b_sig), 1)
            self.assertEqual(b_sig[0]['from_user'], 'ana')
            self.assertEqual(b_sig[0]['from_peer'], a_peer)
            self.assertEqual(b_sig[0]['kind'], 'offer')
            self.assertFalse(any(m.get('type') == 'webrtc_signal' for m in a_msgs))
            await a.disconnect(); await b.disconnect()
        async_to_sync(body)()

    def test_call_join_broadcast_and_present_targeted(self):
        async def body():
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            a_peer = peer_of(await drain(a))
            await drain(b)

            await a.send_json_to({'type': 'call_join'})
            a_msgs, b_msgs = await drain(a), await drain(b)
            joins = [m for m in b_msgs if m.get('type') == 'call_join']
            self.assertEqual(len(joins), 1)
            self.assertEqual(joins[0]['username'], 'ana')
            self.assertEqual(joins[0]['peer'], a_peer)
            self.assertEqual(joins[0]['call_count'], 1)
            # The joiner gets an ack, not the broadcast.
            self.assertFalse(any(m.get('type') == 'call_join' for m in a_msgs))
            self.assertTrue(any(m.get('type') == 'call_joined' for m in a_msgs))

            # call_present must come from a call member, targeted at the joiner.
            await b.send_json_to({'type': 'call_join'})
            await drain(b)
            await b.send_json_to({'type': 'call_present', 'to': a_peer})
            a_msgs, b_msgs = await drain(a), await drain(b)
            self.assertTrue(any(m.get('type') == 'call_present' and m.get('username') == 'john' for m in a_msgs))
            self.assertFalse(any(m.get('type') == 'call_present' for m in b_msgs))
            await a.disconnect(); await b.disconnect()
        async_to_sync(body)()

    def test_call_present_requires_membership(self):
        async def body():
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            a_peer = peer_of(await drain(a))
            await drain(b)
            # john is NOT in the call; his call_present must be dropped.
            await b.send_json_to({'type': 'call_present', 'to': a_peer})
            a_msgs = await drain(a)
            self.assertFalse(any(m.get('type') == 'call_present' for m in a_msgs))
            await a.disconnect(); await b.disconnect()
        async_to_sync(body)()

    def test_call_participant_cap(self):
        async def body():
            with override_settings(MAX_CALL_PARTICIPANTS=1):
                a, _, _ = await self._connect(self.ana)
                b, _, _ = await self._connect(self.john)
                await drain(a); await drain(b)
                await a.send_json_to({'type': 'call_join'})
                await drain(a)
                await b.send_json_to({'type': 'call_join'})
                b_msgs = await drain(b)
                self.assertTrue(any(m.get('type') == 'call_denied' for m in b_msgs))
                self.assertFalse(any(m.get('type') == 'call_joined' for m in b_msgs))
                # ana never learns about the denied join.
                a_msgs = await drain(a)
                self.assertFalse(any(m.get('type') == 'call_join' for m in a_msgs))

                # Once ana leaves, john fits.
                await a.send_json_to({'type': 'call_leave'})
                await drain(a); await drain(b)
                await b.send_json_to({'type': 'call_join'})
                b_msgs = await drain(b)
                self.assertTrue(any(m.get('type') == 'call_joined' for m in b_msgs))
                await a.disconnect(); await b.disconnect()
        async_to_sync(body)()

    def test_disconnect_while_in_call_broadcasts_leave(self):
        async def body():
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            a_peer = peer_of(await drain(a))
            await drain(b)
            await a.send_json_to({'type': 'call_join'})
            await drain(a); await drain(b)
            await a.disconnect()
            b_msgs = await drain(b)
            leaves = [m for m in b_msgs if m.get('type') == 'call_leave']
            self.assertEqual(len(leaves), 1)
            self.assertEqual(leaves[0]['peer'], a_peer)
            self.assertEqual(leaves[0]['call_count'], 0)
            await b.disconnect()
        async_to_sync(body)()

    def test_delete_own_message(self):
        async def body():
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            await drain(a); await drain(b)
            await a.send_json_to({'message': 'oops'})
            mid = next(m['id'] for m in await drain(a) if m.get('type') == 'message')
            await drain(b)

            await a.send_json_to({'type': 'delete_message', 'id': mid})
            b_msgs = await drain(b)
            self.assertTrue(any(
                m.get('type') == 'message_deleted' and m.get('id') == mid for m in b_msgs
            ))
            count = await sync_to_async(Message.objects.filter(id=mid).count)()
            self.assertEqual(count, 0)
            await a.disconnect(); await b.disconnect()
        async_to_sync(body)()

    def test_cannot_delete_someone_elses_message(self):
        async def body():
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            await drain(a); await drain(b)
            await a.send_json_to({'message': 'mine'})
            mid = next(m['id'] for m in await drain(a) if m.get('type') == 'message')
            await drain(b)

            await b.send_json_to({'type': 'delete_message', 'id': mid})
            a_msgs = await drain(a)
            self.assertFalse(any(m.get('type') == 'message_deleted' for m in a_msgs))
            count = await sync_to_async(Message.objects.filter(id=mid).count)()
            self.assertEqual(count, 1)
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

    def test_webrtc_signal_rejects_non_dict_payload_and_self_or_unknown_peer(self):
        async def body():
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            a_peer = peer_of(await drain(a))
            b_peer = peer_of(await drain(b))
            # Non-dict payload must be dropped.
            await a.send_json_to({
                'type': 'webrtc_signal', 'to': b_peer, 'kind': 'offer',
                'payload': 'not-a-dict',
            })
            # Self-signalling must be dropped.
            await a.send_json_to({
                'type': 'webrtc_signal', 'to': a_peer, 'kind': 'offer',
                'payload': {'type': 'offer', 'sdp': 'x'},
            })
            a_msgs, b_msgs = await drain(a), await drain(b)
            self.assertFalse(any(m.get('type') == 'webrtc_signal' for m in a_msgs))
            self.assertFalse(any(m.get('type') == 'webrtc_signal' for m in b_msgs))
            await a.disconnect(); await b.disconnect()
        async_to_sync(body)()

    def test_revoked_private_room_member_cannot_post_messages(self):
        async def body():
            room = await sync_to_async(ChatRoom.objects.create)(
                name='priv', is_private=True, owner=self.ana)
            await sync_to_async(room.members.add)(self.john)
            j, connected, _ = await self._connect(self.john, room='priv')
            self.assertTrue(connected)
            await drain(j)
            # Revoke membership while socket is still open.
            await sync_to_async(room.members.remove)(self.john)
            await j.send_json_to({'message': 'sneaky'})
            msgs = await drain(j)
            self.assertFalse(any(m.get('type') == 'message' for m in msgs))
            count = await sync_to_async(Message.objects.filter(room=room).count)()
            self.assertEqual(count, 0)
            await j.disconnect()
        async_to_sync(body)()

    def test_reply_edit_react_and_pin_message_flows(self):
        async def body():
            self.room.owner = self.ana
            await sync_to_async(self.room.save)()
            a, _, _ = await self._connect(self.ana)
            b, _, _ = await self._connect(self.john)
            await drain(a); await drain(b)

            # 1) Send initial message.
            await a.send_json_to({'message': 'primul mesaj'})
            m1 = next(m for m in await drain(a) if m.get('type') == 'message')
            await drain(b)

            # 2) Reply to m1.
            await b.send_json_to({'message': 'raspuns la primul', 'reply_to': m1['id']})
            reply_msg = next(m for m in await drain(a) if m.get('type') == 'message')
            await drain(b)
            self.assertIsNotNone(reply_msg['reply_to'])
            self.assertEqual(reply_msg['reply_to']['id'], m1['id'])
            self.assertEqual(reply_msg['reply_to']['username'], 'ana')

            # 3) Edit own message (ana edits m1; john cannot edit m1).
            await b.send_json_to({'type': 'edit_message', 'id': m1['id'], 'message': 'hacked'})
            self.assertFalse(any(m.get('type') == 'message_edited' for m in await drain(a)))

            await a.send_json_to({'type': 'edit_message', 'id': m1['id'], 'message': 'primul editat'})
            b_edit = next(m for m in await drain(b) if m.get('type') == 'message_edited')
            await drain(a)
            self.assertEqual(b_edit['id'], m1['id'])
            self.assertEqual(b_edit['message'], 'primul editat')

            # 4) React to m1 (toggle on, then toggle off).
            await b.send_json_to({'type': 'react_message', 'id': m1['id'], 'emoji': '🔥'})
            b_react = next(m for m in await drain(a) if m.get('type') == 'message_reactions')
            await drain(b)
            self.assertEqual(b_react['reactions'], [{'emoji': '🔥', 'count': 1, 'users': ['john']}])

            await b.send_json_to({'type': 'react_message', 'id': m1['id'], 'emoji': '🔥'})
            b_unreact = next(m for m in await drain(a) if m.get('type') == 'message_reactions')
            await drain(b)
            self.assertEqual(b_unreact['reactions'], [])

            # 5) Pin message (john is not owner -> ignored; ana is owner -> pinned).
            await b.send_json_to({'type': 'pin_message', 'id': m1['id']})
            self.assertFalse(any(m.get('type') == 'message_pinned' for m in await drain(a)))

            await a.send_json_to({'type': 'pin_message', 'id': m1['id']})
            b_pin = next(m for m in await drain(b) if m.get('type') == 'message_pinned')
            await drain(a)
            self.assertTrue(b_pin['is_pinned'])
            self.assertEqual(b_pin['id'], m1['id'])

            await a.disconnect(); await b.disconnect()
        async_to_sync(body)()
