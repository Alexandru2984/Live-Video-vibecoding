import json
import logging
import re
import secrets
import threading
import time
from collections import Counter, defaultdict, deque

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.conf import settings
from django.utils import timezone

from .models import ChatRoom, Message

logger = logging.getLogger(__name__)

# --- Limits / anti-abuse -----------------------------------------------------
MAX_MESSAGE_LENGTH = 2000          # characters; longer messages are rejected
RATE_LIMIT_COUNT = 10              # max chat messages...
RATE_LIMIT_WINDOW = 10.0          # ...per this many seconds, per connection
# WebRTC signalling payloads can be large (SDP), but cap them to avoid abuse.
MAX_SIGNAL_BYTES = 100_000

# Per-connection limits for the other inbound message types, per 10s window.
# Every inbound type must appear here: an unthrottled type that fans out via
# group_send lets one client amplify traffic to the whole room.
RATE_LIMITS = {
    'chat': (RATE_LIMIT_COUNT, RATE_LIMIT_WINDOW),
    'typing': (20, 10.0),
    # SDP renegotiation + trickle ICE bursts across a mesh of peers.
    'signal': (200, 10.0),
    'call': (20, 10.0),
    'delete': (10, 10.0),
}

# --- Presence ---------------------------------------------------------------
# Per-process roster: room_group_name -> Counter(username -> open tab count).
# Accurate for the current single-process Daphne deployment. With a multi-worker
# Redis setup each process would only see its own connections, so presence would
# need a shared store (e.g. a Redis set) instead.
_presence_lock = threading.Lock()
_room_members = defaultdict(Counter)


def _presence_add(room, username):
    """Register one connection; returns the user's connection count afterwards."""
    with _presence_lock:
        _room_members[room][username] += 1
        return _room_members[room][username]


def _presence_remove(room, username):
    """Unregister one connection; returns the user's remaining connection count."""
    with _presence_lock:
        members = _room_members.get(room)
        if not members:
            return 0
        members[username] -= 1
        remaining = members[username]
        if remaining <= 0:
            del members[username]
        if not members:
            _room_members.pop(room, None)
        return max(remaining, 0)


def _presence_list(room):
    with _presence_lock:
        return sorted(_room_members.get(room, {}))


# --- Video call membership ---------------------------------------------------
# Peers are identified by a per-connection id, not by username: the same
# account may be open in several tabs/devices, and username-keyed signalling
# would deliver offers to all of them at once. Same single-process caveat as
# the presence roster above.
PEER_ID_RE = re.compile(r'^[0-9a-f]{16}$')
_call_members = defaultdict(set)


def _peer_group(room_group_name, peer_id):
    """Per-connection channel group for targeted WebRTC signalling."""
    return f'{room_group_name}.p.{peer_id}'


def _call_add(room, peer_id, cap):
    """Try to join the call; returns (joined, participant_count)."""
    with _presence_lock:
        members = _call_members[room]
        if peer_id in members:
            return True, len(members)
        if len(members) >= cap:
            return False, len(members)
        members.add(peer_id)
        return True, len(members)


def _call_remove(room, peer_id):
    """Leave the call; returns (was_member, participant_count)."""
    with _presence_lock:
        members = _call_members.get(room)
        if not members or peer_id not in members:
            return False, len(members or ())
        members.discard(peer_id)
        if not members:
            _call_members.pop(room, None)
            return True, 0
        return True, len(members)


def _call_count(room):
    with _presence_lock:
        return len(_call_members.get(room, ()))


class ChatConsumer(AsyncWebsocketConsumer):
    """Per-room chat + WebRTC signalling.

    Security model:
      * Only authenticated users may connect at all (anonymous users could
        previously read every broadcast in a room).
      * The room must already exist (rooms are created through the HTTP view,
        not by merely opening a socket).
      * Outbound chat is rate-limited and length-capped per connection.
      * Identity (``from_user``) and system notification text are always
        derived server-side, never trusted from the client payload.
    """

    # Set only after the connection is fully accepted and registered; disconnect()
    # runs for rejected handshakes too, and must not tear down what never existed.
    joined = False

    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f'chat_{self.room_name}'
        self.user = self.scope.get('user')
        self._buckets = {}

        # 1) Authentication is mandatory.
        if not self.user or not self.user.is_authenticated:
            logger.info('Rejected anonymous WebSocket connection to %s', self.room_name)
            await self.close(code=4401)  # 4401 = unauthorized (app-defined)
            return

        # 2) The room must exist; we do not auto-create rooms from a socket.
        #    Private rooms additionally require membership.
        access = await self.room_access()
        if access is None:
            logger.info('Rejected connection to missing room %s', self.room_name)
            await self.close(code=4404)  # 4404 = not found (app-defined)
            return
        if not access:
            logger.info('Rejected %s from private room %s', self.user.username, self.room_name)
            await self.close(code=4403)  # 4403 = forbidden (app-defined)
            return

        self.peer_id = secrets.token_hex(8)
        self.in_call = False
        self.peer_group_name = _peer_group(self.room_group_name, self.peer_id)
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.channel_layer.group_add(self.peer_group_name, self.channel_name)
        await self.accept()
        self.joined = True
        logger.debug('User %s connected to room %s', self.user.username, self.room_name)

        # Tell this connection who it is (peer id for signalling) and the
        # current call size, before any broadcasts arrive.
        await self.send(text_data=json.dumps({
            'type': 'welcome',
            'peer_id': self.peer_id,
            'username': self.user.username,
            'call_count': _call_count(self.room_group_name),
        }))

        # Announce the user only on their first connection (not per extra tab).
        if _presence_add(self.room_group_name, self.user.username) == 1:
            await self.channel_layer.group_send(
                self.room_group_name,
                {'type': 'user_join', 'username': self.user.username},
            )
        await self.broadcast_presence()

    async def disconnect(self, close_code):
        if not self.joined:
            return  # rejected handshake: nothing was registered
        if self.in_call:
            _, count = _call_remove(self.room_group_name, self.peer_id)
            await self.channel_layer.group_send(
                self.room_group_name,
                {'type': 'call_leave', 'username': self.user.username,
                 'peer': self.peer_id, 'call_count': count},
            )
        if _presence_remove(self.room_group_name, self.user.username) == 0:
            await self.channel_layer.group_send(
                self.room_group_name,
                {'type': 'user_leave', 'username': self.user.username},
            )
        await self.broadcast_presence()
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        await self.channel_layer.group_discard(self.peer_group_name, self.channel_name)

    async def broadcast_presence(self):
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'presence', 'users': _presence_list(self.room_group_name)},
        )

    async def presence(self, event):
        await self.send(text_data=json.dumps({
            'type': 'presence',
            'users': event['users'],
            'count': len(event['users']),
        }))

    # -- inbound ------------------------------------------------------------
    async def receive(self, text_data=None, bytes_data=None):
        if not self.user or not self.user.is_authenticated:
            return  # accepted sockets are always authenticated, but be safe

        if text_data is None or len(text_data) > MAX_SIGNAL_BYTES:
            return

        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            logger.debug('Invalid JSON on socket for room %s', self.room_name)
            return
        if not isinstance(data, dict):
            return

        message_type = data.get('type', 'message')

        handlers = {
            'webrtc_signal': self.handle_webrtc_signal,
            'call_join': self.handle_call_join,
            'call_leave': self.handle_call_leave,
            'call_present': self.handle_call_present,
            'typing': self.handle_typing,
            'delete_message': self.handle_delete_message,
        }
        handler = handlers.get(message_type)
        if handler is not None:
            await handler(data)
        else:
            await self.handle_chat_message(data)

    async def handle_chat_message(self, data):
        message = (data.get('message') or '').strip()
        if not message:
            return
        if len(message) > MAX_MESSAGE_LENGTH:
            await self.send_error(f'Mesajul depășește {MAX_MESSAGE_LENGTH} de caractere.')
            return
        if self.is_rate_limited():
            await self.send_error('Trimiți mesaje prea repede. Așteaptă o secundă.')
            return

        saved = await self.save_message(message)
        if saved is None:
            return  # room vanished mid-session; nothing to broadcast
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'id': saved.id,
                'message': message,
                'username': self.user.username,
                'timestamp': self.get_timestamp(),
                'iso': timezone.localtime(saved.timestamp).isoformat(),
            },
        )

    # -- WebRTC mesh signalling (per-peer, identity stamped server-side) -----
    async def handle_webrtc_signal(self, data):
        """Relay one SDP/ICE message to a single target peer in the room."""
        to = data.get('to')
        kind = data.get('kind')
        payload = data.get('payload')
        if not isinstance(to, str) or not PEER_ID_RE.match(to):
            return
        if kind not in ('offer', 'answer', 'candidate') or payload is None:
            return
        if self.is_rate_limited('signal'):
            return  # drop silently; errors here would only add traffic
        await self.send_to_peer(to, {
            'type': 'webrtc_signal',
            'from_user': self.user.username,
            'from_peer': self.peer_id,
            'kind': kind,
            'payload': payload,
        })

    async def handle_call_join(self, data):
        # Announce to the room that we joined the video call. Members already in
        # the call answer with `call_present` so we know whom to offer to.
        if self.is_rate_limited('call') or self.in_call:
            return
        joined, count = _call_add(
            self.room_group_name, self.peer_id, settings.MAX_CALL_PARTICIPANTS,
        )
        if not joined:
            await self.send(text_data=json.dumps({
                'type': 'call_denied',
                'error': f'Apelul este plin (maxim {settings.MAX_CALL_PARTICIPANTS} participanți).',
                'call_count': count,
            }))
            return
        self.in_call = True
        # Ack directly to the joiner (the broadcast below skips its own peer).
        await self.send(text_data=json.dumps({'type': 'call_joined', 'call_count': count}))
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'call_join', 'username': self.user.username,
             'peer': self.peer_id, 'call_count': count},
        )

    async def handle_call_leave(self, data):
        if self.is_rate_limited('call') or not self.in_call:
            return
        self.in_call = False
        _, count = _call_remove(self.room_group_name, self.peer_id)
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'call_leave', 'username': self.user.username,
             'peer': self.peer_id, 'call_count': count},
        )

    async def handle_call_present(self, data):
        # Targeted reply to a joiner: "I'm already in the call".
        if self.is_rate_limited('call') or not self.in_call:
            return
        to = data.get('to')
        if isinstance(to, str) and PEER_ID_RE.match(to):
            await self.send_to_peer(to, {
                'type': 'call_present',
                'username': self.user.username,
                'peer': self.peer_id,
            })

    async def handle_delete_message(self, data):
        """Delete one of your own messages (hard delete) and tell the room."""
        if self.is_rate_limited('delete'):
            return
        message_id = data.get('id')
        if not isinstance(message_id, int) or message_id <= 0:
            return
        if await self.delete_own_message(message_id):
            await self.channel_layer.group_send(
                self.room_group_name,
                {'type': 'message_deleted', 'id': message_id},
            )

    async def handle_typing(self, data):
        if self.is_rate_limited('typing'):
            return
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'typing',
                'username': self.user.username,
                'is_typing': bool(data.get('is_typing')),
            },
        )

    # -- outbound (group -> socket) -----------------------------------------
    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'message',
            'id': event['id'],
            'message': event['message'],
            'username': event['username'],
            'timestamp': event['timestamp'],
            'iso': event['iso'],
        }))

    async def user_join(self, event):
        await self.send(text_data=json.dumps({
            'type': 'user_join',
            'username': event['username'],
            'message': f"{event['username']} s-a alăturat conversației",
        }))

    async def user_leave(self, event):
        await self.send(text_data=json.dumps({
            'type': 'user_leave',
            'username': event['username'],
            'message': f"{event['username']} a părăsit conversația",
        }))

    async def message_deleted(self, event):
        await self.send(text_data=json.dumps({
            'type': 'message_deleted',
            'id': event['id'],
        }))

    async def room_deleted(self, event):
        # Sent by the owner's HTTP delete view; clients navigate away.
        await self.send(text_data=json.dumps({'type': 'room_deleted'}))

    async def typing(self, event):
        if event['username'] == self.user.username:
            return  # don't echo my own typing state back to me
        await self.send(text_data=json.dumps({
            'type': 'typing',
            'username': event['username'],
            'is_typing': event['is_typing'],
        }))

    async def webrtc_signal(self, event):
        # Targeted (sent to the recipient's peer group only).
        await self.send(text_data=json.dumps({
            'type': 'webrtc_signal',
            'from_user': event['from_user'],
            'from_peer': event['from_peer'],
            'kind': event['kind'],
            'payload': event['payload'],
        }))

    async def call_join(self, event):
        if event['peer'] != self.peer_id:
            await self.send(text_data=json.dumps({
                'type': 'call_join', 'username': event['username'],
                'peer': event['peer'], 'call_count': event['call_count'],
            }))

    async def call_leave(self, event):
        if event['peer'] != self.peer_id:
            await self.send(text_data=json.dumps({
                'type': 'call_leave', 'username': event['username'],
                'peer': event['peer'], 'call_count': event['call_count'],
            }))

    async def call_present(self, event):
        # Targeted; the recipient is always a different peer, no self-check.
        await self.send(text_data=json.dumps({
            'type': 'call_present', 'username': event['username'],
            'peer': event['peer'],
        }))

    # -- helpers ------------------------------------------------------------
    async def send_to_peer(self, peer_id, message):
        await self.channel_layer.group_send(
            _peer_group(self.room_group_name, peer_id), message,
        )

    async def send_error(self, message):
        await self.send(text_data=json.dumps({'type': 'error', 'error': message}))

    def is_rate_limited(self, bucket='chat'):
        limit, window = RATE_LIMITS[bucket]
        times = self._buckets.setdefault(bucket, deque())
        now = time.monotonic()
        window_start = now - window
        while times and times[0] < window_start:
            times.popleft()
        if len(times) >= limit:
            return True
        times.append(now)
        return False

    @database_sync_to_async
    def room_access(self):
        """None: room missing. False: private, not a member. True: welcome."""
        room = ChatRoom.objects.filter(name=self.room_name).first()
        if room is None:
            return None
        return room.can_access(self.user)

    @database_sync_to_async
    def delete_own_message(self, message_id):
        # Ownership and room scoping enforced in the query itself.
        deleted, _ = Message.objects.filter(
            id=message_id, user=self.user, room__name=self.room_name,
        ).delete()
        return deleted > 0

    @database_sync_to_async
    def save_message(self, message):
        room = ChatRoom.objects.filter(name=self.room_name).first()
        if room is None:
            # Room was deleted between connect and now; drop silently.
            return None
        return Message.objects.create(room=room, user=self.user, content=message)

    @staticmethod
    def get_timestamp():
        return timezone.localtime().strftime('%H:%M')
