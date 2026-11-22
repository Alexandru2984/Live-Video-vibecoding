from django.db import models
from django.contrib.auth.models import User

ALLOWED_REACTIONS = ('👍', '❤️', '😂', '🔥', '😮', '😢', '🎉', '💯')


class ChatRoom(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    topic = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Rooms created before ownership existed have owner=None (nobody can
    # delete them except via the admin, same as before).
    owner = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='owned_rooms',
    )
    is_private = models.BooleanField(default=False)
    members = models.ManyToManyField(User, blank=True, related_name='joined_rooms')
    # Incrementing invite_version invalidates all previously minted invite links.
    invite_version = models.PositiveIntegerField(default=1)

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
    edited_at = models.DateTimeField(null=True, blank=True)
    is_pinned = models.BooleanField(default=False)
    reply_to = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replies',
    )

    class Meta:
        # Pagination is keyed on id (monotonic with auto_now_add timestamps,
        # and unambiguous where same-moment timestamps would tie).
        ordering = ['id']
        indexes = [
            models.Index(fields=['room', '-id'], name='chat_msg_room_newest'),
        ]

    def serialize_reply(self):
        if not self.reply_to_id or not self.reply_to:
            return None
        return {
            'id': self.reply_to.id,
            'username': self.reply_to.user.username,
            'message': self.reply_to.content[:120],
        }

    def serialize_reactions(self):
        grouped = {}
        for r in self.reactions.all():
            users = grouped.setdefault(r.emoji, [])
            users.append(r.user.username)
        return [
            {'emoji': emoji, 'count': len(users), 'users': sorted(users)}
            for emoji, users in grouped.items()
        ]

    def __str__(self):
        return f'{self.user.username}: {self.content[:50]}'


class MessageReaction(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='message_reactions')
    emoji = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(
                fields=['message', 'user', 'emoji'],
                name='chat_unique_msg_user_reaction',
            ),
        ]

    def __str__(self):
        return f'{self.user.username} {self.emoji} on #{self.message_id}'


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    bio = models.CharField(max_length=300, blank=True)
    status_emoji = models.CharField(max_length=16, blank=True)
    status_text = models.CharField(max_length=80, blank=True)
    avatar_color = models.CharField(max_length=7, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Profil {self.user.username}'


class Poll(models.Model):
    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='polls')
    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_polls')
    question = models.CharField(max_length=250)
    is_closed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']

    def serialize(self):
        options_out = []
        total_votes = 0
        for opt in self.options.all():
            voters = sorted(v.user.username for v in opt.votes.all())
            count = len(voters)
            total_votes += count
            options_out.append({
                'id': opt.id,
                'text': opt.text,
                'count': count,
                'voters': voters,
            })
        return {
            'id': self.id,
            'question': self.question,
            'creator': self.creator.username,
            'is_closed': self.is_closed,
            'total_votes': total_votes,
            'options': options_out,
        }

    def __str__(self):
        return f'Poll #{self.id}: {self.question[:50]}'


class PollOption(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name='options')
    text = models.CharField(max_length=120)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.text


class PollVote(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name='votes')
    option = models.ForeignKey(PollOption, on_delete=models.CASCADE, related_name='votes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='poll_votes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['poll', 'user'],
                name='chat_unique_poll_user_vote',
            ),
        ]

    def __str__(self):
        return f'{self.user.username} -> {self.option_id} (poll {self.poll_id})'
