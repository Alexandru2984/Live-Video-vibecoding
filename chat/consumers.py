import json
import logging
import time
from collections import deque

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

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()
        logger.debug('User %s connected to room %s', self.user.username, self.room_name)

        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'user_join', 'username': self.user.username},
        )

    async def disconnect(self, close_code):
        # group_add only ran for accepted (authenticated) connections.
        if getattr(self, 'user', None) and self.user.is_authenticated:
            await self.channel_layer.group_send(
                self.room_group_name,
                {'type': 'user_leave', 'username': self.user.username},
            )
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

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
            'video_offer': self.handle_video_offer,
            'video_answer': self.handle_video_answer,
            'ice_candidate': self.handle_ice_candidate,
            'video_call_request': self.handle_video_call_request,
            'video_call_end': self.handle_video_call_end,
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

    # -- WebRTC signalling (identity stamped server-side) -------------------
    async def handle_video_offer(self, data):
        offer = data.get('offer')
        if offer is None:
            return
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'video_offer', 'offer': offer, 'from_user': self.user.username},
        )

    async def handle_video_answer(self, data):
        answer = data.get('answer')
        if answer is None:
            return
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'video_answer', 'answer': answer, 'from_user': self.user.username},
        )

    async def handle_ice_candidate(self, data):
        candidate = data.get('candidate')
        if candidate is None:
            return
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'ice_candidate', 'candidate': candidate, 'from_user': self.user.username},
        )

    async def handle_video_call_request(self, data):
        # The notification text is generated here so a client cannot inject
        # arbitrary content that other browsers would render.
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'video_call_request',
                'message': f'{self.user.username} a pornit videochat-ul. Click pentru a te alătura!',
                'from_user': self.user.username,
            },
        )

    async def handle_video_call_end(self, data):
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'video_call_end', 'from_user': self.user.username},
        )

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

    async def video_offer(self, event):
        if event['from_user'] != self.user.username:
            await self.send(text_data=json.dumps({
                'type': 'video_offer', 'offer': event['offer'], 'from_user': event['from_user'],
            }))

    async def video_answer(self, event):
        if event['from_user'] != self.user.username:
            await self.send(text_data=json.dumps({
                'type': 'video_answer', 'answer': event['answer'], 'from_user': event['from_user'],
            }))

    async def ice_candidate(self, event):
        if event['from_user'] != self.user.username:
            await self.send(text_data=json.dumps({
                'type': 'ice_candidate', 'candidate': event['candidate'], 'from_user': event['from_user'],
            }))

    async def video_call_request(self, event):
        if event['from_user'] != self.user.username:
            await self.send(text_data=json.dumps({
                'type': 'video_call_request',
                'message': event['message'],
                'from_user': event['from_user'],
            }))

    async def video_call_end(self, event):
        if event['from_user'] != self.user.username:
            await self.send(text_data=json.dumps({
                'type': 'video_call_end', 'from_user': event['from_user'],
            }))

    # -- helpers ------------------------------------------------------------
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
