from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from chat.models import ChatRoom, Message

User = get_user_model()


class RotateCredentialsCommandTests(TestCase):
    def test_rotates_named_user_and_prints_once(self):
        user = User.objects.create_user('bob', password='old-password')
        old_hash = user.password
        out = StringIO()
        call_command('rotate_credentials', 'bob', stdout=out)
        user.refresh_from_db()
        self.assertNotEqual(user.password, old_hash)
        self.assertIn('bob', out.getvalue())

    def test_missing_user_raises(self):
        with self.assertRaises(CommandError):
            call_command('rotate_credentials', 'ghost', stdout=StringIO())

    def test_requires_a_selector(self):
        with self.assertRaises(CommandError):
            call_command('rotate_credentials', stdout=StringIO())

    def test_refuses_short_passwords(self):
        User.objects.create_user('x', password='p')
        with self.assertRaises(CommandError):
            call_command('rotate_credentials', '--all', '--length', '8', stdout=StringIO())

    def test_staff_only_leaves_normal_users(self):
        staff = User.objects.create_user('adm', password='a', is_staff=True)
        normal = User.objects.create_user('norm', password='b')
        staff_hash, normal_hash = staff.password, normal.password
        call_command('rotate_credentials', '--staff', stdout=StringIO())
        staff.refresh_from_db(); normal.refresh_from_db()
        self.assertNotEqual(staff.password, staff_hash)
        self.assertEqual(normal.password, normal_hash)


class PurgeMessagesCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u', password='p')
        self.room = ChatRoom.objects.create(name='general')
        self.old = Message.objects.create(room=self.room, user=self.user, content='old')
        Message.objects.filter(pk=self.old.pk).update(
            timestamp=timezone.now() - timedelta(days=100))
        self.fresh = Message.objects.create(room=self.room, user=self.user, content='new')

    def test_purges_only_older_than_window(self):
        out = StringIO()
        call_command('purge_messages', '--days', '90', stdout=out)
        self.assertFalse(Message.objects.filter(pk=self.old.pk).exists())
        self.assertTrue(Message.objects.filter(pk=self.fresh.pk).exists())
        self.assertIn('Deleted 1', out.getvalue())

    def test_dry_run_deletes_nothing(self):
        out = StringIO()
        call_command('purge_messages', '--days', '90', '--dry-run', stdout=out)
        self.assertEqual(Message.objects.count(), 2)
        self.assertIn('Would delete 1', out.getvalue())

    def test_room_filter(self):
        other = ChatRoom.objects.create(name='other')
        stale = Message.objects.create(room=other, user=self.user, content='x')
        Message.objects.filter(pk=stale.pk).update(
            timestamp=timezone.now() - timedelta(days=100))
        call_command('purge_messages', '--days', '90', '--room', 'other', stdout=StringIO())
        self.assertFalse(Message.objects.filter(pk=stale.pk).exists())
        self.assertTrue(Message.objects.filter(pk=self.old.pk).exists())

    def test_rejects_zero_days(self):
        with self.assertRaises(CommandError):
            call_command('purge_messages', '--days', '0', stdout=StringIO())
