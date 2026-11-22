from django.contrib import admin
from .models import ChatRoom, Message, MessageReaction


@admin.register(ChatRoom)
class ChatRoomAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'owner', 'is_private', 'invite_version', 'created_at')
    list_filter = ('is_private', 'created_at')
    search_fields = ('name', 'description', 'owner__username')
    readonly_fields = ('created_at',)
    filter_horizontal = ('members',)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('user', 'room', 'content_preview', 'is_pinned', 'edited_at', 'timestamp')
    list_filter = ('room', 'is_pinned', 'timestamp', 'user')
    search_fields = ('content', 'user__username', 'room__name')
    readonly_fields = ('timestamp', 'edited_at')

    def content_preview(self, obj):
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content
    content_preview.short_description = 'Conținut'


@admin.register(MessageReaction)
class MessageReactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'emoji', 'message', 'created_at')
    list_filter = ('emoji', 'created_at')
    search_fields = ('user__username', 'message__content')
    readonly_fields = ('created_at',)
