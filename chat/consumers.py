import hashlib
import json
import logging
import threading
import time
from collections import Counter, defaultdict, deque

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone

from .models import ChatRoom, Message

logger = logging.getLogger(__name__)

# --- Limits / anti-abuse -----------------------------------------------------
MAX_MESSAGE_LENGTH = 2000          # characters; longer messages are rejected
RATE_LIMIT_COUNT = 10              # max messages...
RATE_LIMIT_WINDOW = 10.0          # ...per this many seconds, per connection
# WebRTC signalling payloads can be large (SDP), but cap them to avoid abuse.
MAX_SIGNAL_BYTES = 100_000

# --- Presence ---------------------------------------------------------------
# Per-process roster: room_group_name -> Counter(username -> open tab count).
# Accurate for the current single-process Daphne deployment. With a multi-worker
# Redis setup each process would only see its own connections, so presence would
# need a shared store (e.g. a Redis set) instead.
_presence_lock = threading.Lock()
_room_members = defaultdict(Counter)


def _presence_add(room, username):
    with _presence_lock:
        _room_members[room][username] += 1


def _presence_remove(room, username):
    with _presence_lock:
        members = _room_members.get(room)
        if not members:
            return
        members[username] -= 1
        if members[username] <= 0:
            del members[username]
        if not members:
            _room_members.pop(room, None)


def _presence_list(room):
    with _presence_lock:
        return sorted(_room_members.get(room, {}))


def _user_group(room_group_name, username):
    """Per-user channel group, used to deliver targeted WebRTC signalling.

    Usernames can contain characters that are invalid in channel group names,
    so we hash them into a safe, fixed suffix.
    """
    digest = hashlib.sha1(username.encode('utf-8')).hexdigest()[:20]
    return f'{room_group_name}.u.{digest}'


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

    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f'chat_{self.room_name}'
        self.user = self.scope.get('user')
        self._message_times = deque()

        # 1) Authentication is mandatory.
        if not self.user or not self.user.is_authenticated:
            logger.info('Rejected anonymous WebSocket connection to %s', self.room_name)
            await self.close(code=4401)  # 4401 = unauthorized (app-defined)
            return

        # 2) The room must exist; we do not auto-create rooms from a socket.
        if not await self.room_exists():
            logger.info('Rejected connection to missing room %s', self.room_name)
            await self.close(code=4404)  # 4404 = not found (app-defined)
            return

        self.user_group_name = _user_group(self.room_group_name, self.user.username)
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.accept()
        logger.debug('User %s connected to room %s', self.user.username, self.room_name)

        _presence_add(self.room_group_name, self.user.username)
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'user_join', 'username': self.user.username},
        )
        await self.broadcast_presence()

    async def disconnect(self, close_code):
        # group_add only ran for accepted (authenticated) connections.
        if getattr(self, 'user', None) and self.user.is_authenticated:
            _presence_remove(self.room_group_name, self.user.username)
            await self.channel_layer.group_send(
                self.room_group_name,
                {'type': 'user_leave', 'username': self.user.username},
            )
            await self.broadcast_presence()
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)

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

        await self.save_message(message)
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'message': message,
                'username': self.user.username,
                'timestamp': self.get_timestamp(),
            },
        )

    # -- WebRTC mesh signalling (per-peer, identity stamped server-side) -----
    async def handle_webrtc_signal(self, data):
        """Relay one SDP/ICE message to a single target peer in the room."""
        to = data.get('to')
        kind = data.get('kind')
        payload = data.get('payload')
        if not isinstance(to, str) or kind not in ('offer', 'answer', 'candidate'):
            return
        if payload is None:
            return
        await self.send_to_user(to, {
            'type': 'webrtc_signal',
            'from_user': self.user.username,
            'kind': kind,
            'payload': payload,
        })

    async def handle_call_join(self, data):
        # Announce to the room that we joined the video call. Members already in
        # the call answer with `call_present` so we know whom to offer to.
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'call_join', 'username': self.user.username},
        )

    async def handle_call_leave(self, data):
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'call_leave', 'username': self.user.username},
        )

    async def handle_call_present(self, data):
        # Targeted reply to a joiner: "I'm already in the call".
        to = data.get('to')
        if isinstance(to, str):
            await self.send_to_user(to, {'type': 'call_present', 'username': self.user.username})

    async def handle_typing(self, data):
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
            'message': event['message'],
            'username': event['username'],
            'timestamp': event['timestamp'],
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

    async def typing(self, event):
        if event['username'] == self.user.username:
            return  # don't echo my own typing state back to me
        await self.send(text_data=json.dumps({
            'type': 'typing',
            'username': event['username'],
            'is_typing': event['is_typing'],
        }))

    async def webrtc_signal(self, event):
        # Targeted (sent to the recipient's user group only).
        await self.send(text_data=json.dumps({
            'type': 'webrtc_signal',
            'from_user': event['from_user'],
            'kind': event['kind'],
            'payload': event['payload'],
        }))

    async def call_join(self, event):
        if event['username'] != self.user.username:
            await self.send(text_data=json.dumps({
                'type': 'call_join', 'username': event['username'],
            }))

    async def call_leave(self, event):
        if event['username'] != self.user.username:
            await self.send(text_data=json.dumps({
                'type': 'call_leave', 'username': event['username'],
            }))

    async def call_present(self, event):
        # Targeted; the recipient is always a different user, no self-check.
        await self.send(text_data=json.dumps({
            'type': 'call_present', 'username': event['username'],
        }))

    # -- helpers ------------------------------------------------------------
    async def send_to_user(self, username, message):
        await self.channel_layer.group_send(
            _user_group(self.room_group_name, username), message,
        )

    async def send_error(self, message):
        await self.send(text_data=json.dumps({'type': 'error', 'error': message}))

    def is_rate_limited(self):
        now = time.monotonic()
        window_start = now - RATE_LIMIT_WINDOW
        while self._message_times and self._message_times[0] < window_start:
            self._message_times.popleft()
        if len(self._message_times) >= RATE_LIMIT_COUNT:
            return True
        self._message_times.append(now)
        return False

    @database_sync_to_async
    def room_exists(self):
        return ChatRoom.objects.filter(name=self.room_name).exists()

    @database_sync_to_async
    def save_message(self, message):
        room = ChatRoom.objects.filter(name=self.room_name).first()
        if room is None:
            # Room was deleted between connect and now; drop silently.
            return
        Message.objects.create(room=room, user=self.user, content=message)

    @staticmethod
    def get_timestamp():
        return timezone.localtime().strftime('%H:%M')
