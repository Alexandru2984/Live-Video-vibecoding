from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

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
