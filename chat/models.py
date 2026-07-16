from django.db import models
from django.contrib.auth.models import User


class ChatRoom(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Rooms created before ownership existed have owner=None (nobody can
    # delete them except via the admin, same as before).
    owner = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='owned_rooms',
    )
    is_private = models.BooleanField(default=False)
    members = models.ManyToManyField(User, blank=True, related_name='joined_rooms')

    def can_access(self, user):
        """Private rooms are visible to their owner and invited members only."""
        if not self.is_private:
            return True
        if not user.is_authenticated:
            return False
        return user.id == self.owner_id or self.members.filter(pk=user.pk).exists()

    def __str__(self):
        return self.name


class Message(models.Model):
    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='messages')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Pagination is keyed on id (monotonic with auto_now_add timestamps,
        # and unambiguous where same-moment timestamps would tie).
        ordering = ['id']
        indexes = [
            models.Index(fields=['room', '-id'], name='chat_msg_room_newest'),
        ]

    def __str__(self):
        return f'{self.user.username}: {self.content[:50]}'
