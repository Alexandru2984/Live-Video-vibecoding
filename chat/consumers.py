import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import User
from .models import ChatRoom, Message

logger = logging.getLogger(__name__)


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f'chat_{self.room_name}'
        
        logger.info(f"User connecting to room: {self.room_name}")
        
        # Adaugă utilizatorul la grupa camerei
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        logger.info(f"WebSocket connection accepted for room: {self.room_name}")
        
        # Anunță că un utilizator s-a conectat
        if self.scope["user"].is_authenticated:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'user_join',
                    'username': self.scope["user"].username,
                }
            )

    async def disconnect(self, close_code):
        logger.info(f"User disconnecting from room: {self.room_name}")
        
        # Anunță că un utilizator s-a deconectat
        if self.scope["user"].is_authenticated:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'user_leave',
                    'username': self.scope["user"].username,
                }
            )
        
        # Elimină utilizatorul din grupa camerei
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        logger.info(f"Received message: {text_data}")
        
        if not self.scope["user"].is_authenticated:
            logger.warning("Unauthenticated user tried to send message")
            await self.send(text_data=json.dumps({
                'error': 'Trebuie să fii autentificat pentru a trimite mesaje'
            }))
            return
            
        try:
            text_data_json = json.loads(text_data)
            message_type = text_data_json.get('type', 'message')
            
            # Handle different message types
            if message_type == 'video_offer':
                await self.handle_video_offer(text_data_json)
            elif message_type == 'video_answer':
                await self.handle_video_answer(text_data_json)
            elif message_type == 'ice_candidate':
                await self.handle_ice_candidate(text_data_json)
            elif message_type == 'video_call_request':
                await self.handle_video_call_request(text_data_json)
            elif message_type == 'video_call_end':
                await self.handle_video_call_end(text_data_json)
            else:
                # Regular chat message
                message = text_data_json.get('message', '').strip()
                
                if not message:
                    logger.warning("Empty message received")
                    return
                
                logger.info(f"Processing message from {self.scope['user'].username}: {message}")
                
                # Salvează mesajul în baza de date
                await self.save_message(message)
                
                # Trimite mesajul la toți membrii grupei
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'chat_message',
                        'message': message,
                        'username': self.scope["user"].username,
                        'timestamp': self.get_timestamp()
                    }
                )
                logger.info(f"Message sent to group: {self.room_group_name}")
            
        except json.JSONDecodeError:
            logger.error("Invalid JSON received")
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    # Video WebRTC handlers
    async def handle_video_offer(self, data):
        """Handle WebRTC video offer"""
        logger.info(f"Handling video offer from {self.scope['user'].username}")
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'video_offer',
                'offer': data['offer'],
                'from_user': self.scope["user"].username,
            }
        )

    async def handle_video_answer(self, data):
        """Handle WebRTC video answer"""
        logger.info(f"Handling video answer from {self.scope['user'].username}")
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'video_answer',
                'answer': data['answer'],
                'from_user': self.scope["user"].username,
            }
        )

    async def handle_ice_candidate(self, data):
        """Handle WebRTC ICE candidate"""
        logger.info(f"Handling ICE candidate from {self.scope['user'].username}")
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'ice_candidate',
                'candidate': data['candidate'],
                'from_user': self.scope["user"].username,
            }
        )

    async def handle_video_call_request(self, data):
        """Handle video call request notification"""
        logger.info(f"Handling video call request from {self.scope['user'].username}")
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'video_call_request',
                'message': data['message'],
                'from_user': self.scope["user"].username,
            }
        )

    async def handle_video_call_end(self, data):
        """Handle video call end notification"""
        logger.info(f"Handling video call end from {self.scope['user'].username}")
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'video_call_end',
                'from_user': self.scope["user"].username,
            }
        )

    async def chat_message(self, event):
        logger.info(f"Sending chat message: {event}")
        # Trimite mesajul la WebSocket
        await self.send(text_data=json.dumps({
            'type': 'message',
            'message': event['message'],
            'username': event['username'],
            'timestamp': event['timestamp']
        }))

    async def user_join(self, event):
        logger.info(f"User joined: {event['username']}")
        # Anunță că un utilizator s-a alăturat
        await self.send(text_data=json.dumps({
            'type': 'user_join',
            'username': event['username'],
            'message': f"{event['username']} s-a alăturat conversației"
        }))

    async def user_leave(self, event):
        logger.info(f"User left: {event['username']}")
        # Anunță că un utilizator a plecat
        await self.send(text_data=json.dumps({
            'type': 'user_leave',
            'username': event['username'],
            'message': f"{event['username']} a părăsit conversația"
        }))

    # WebRTC message handlers
    async def video_offer(self, event):
        """Send video offer to WebSocket"""
        logger.info(f"Sending video offer from {event['from_user']}")
        # Don't send to the sender
        if event['from_user'] != self.scope["user"].username:
            await self.send(text_data=json.dumps({
                'type': 'video_offer',
                'offer': event['offer'],
                'from_user': event['from_user']
            }))

    async def video_answer(self, event):
        """Send video answer to WebSocket"""
        logger.info(f"Sending video answer from {event['from_user']}")
        # Don't send to the sender
        if event['from_user'] != self.scope["user"].username:
            await self.send(text_data=json.dumps({
                'type': 'video_answer',
                'answer': event['answer'],
                'from_user': event['from_user']
            }))

    async def ice_candidate(self, event):
        """Send ICE candidate to WebSocket"""
        logger.info(f"Sending ICE candidate from {event['from_user']}")
        # Don't send to the sender
        if event['from_user'] != self.scope["user"].username:
            await self.send(text_data=json.dumps({
                'type': 'ice_candidate',
                'candidate': event['candidate'],
                'from_user': event['from_user']
            }))

    async def video_call_request(self, event):
        """Send video call request notification"""
        logger.info(f"Sending video call request from {event['from_user']}")
        await self.send(text_data=json.dumps({
            'type': 'video_call_request',
            'message': event['message'],
            'from_user': event['from_user']
        }))

    async def video_call_end(self, event):
        """Send video call end notification"""
        logger.info(f"Sending video call end from {event['from_user']}")
        await self.send(text_data=json.dumps({
            'type': 'video_call_end',
            'from_user': event['from_user']
        }))

    @database_sync_to_async
    def save_message(self, message):
        """Salvează mesajul în baza de date"""
        try:
            room = ChatRoom.objects.get(name=self.room_name)
            Message.objects.create(
                room=room,
                user=self.scope["user"],
                content=message
            )
        except ChatRoom.DoesNotExist:
            # Creează camera dacă nu există
            room = ChatRoom.objects.create(
                name=self.room_name,
                description=f"Camera {self.room_name}"
            )
            Message.objects.create(
                room=room,
                user=self.scope["user"],
                content=message
            )

    def get_timestamp(self):
        """Returnează timestamp-ul curent formatat"""
        from datetime import datetime
        return datetime.now().strftime('%H:%M')
