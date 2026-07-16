"""Rotate user passwords to strong random values and print them once.

Examples:
    python manage.py rotate_credentials admin ana john
    python manage.py rotate_credentials --staff
    python manage.py rotate_credentials --all --length 24

The generated passwords are shown a single time. Copy them to your password
manager immediately — they are hashed in the database and cannot be recovered.
"""
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

# Unambiguous alphabet (no O/0, l/1) plus a few symbols.
_ALPHABET = (
    'ABCDEFGHJKLMNPQRSTUVWXYZ'
    'abcdefghijkmnopqrstuvwxyz'
    '23456789'
    '!@#$%^&*-_=+'
)


def generate_password(length):
    return ''.join(secrets.choice(_ALPHABET) for _ in range(length))


class Command(BaseCommand):
    help = 'Rotate user passwords to strong random values and print them once.'

    def add_arguments(self, parser):
        parser.add_argument('usernames', nargs='*', help='Specific usernames to rotate.')
        parser.add_argument('--all', action='store_true', dest='all_users',
                            help='Rotate every user account.')
        parser.add_argument('--staff', action='store_true',
                            help='Rotate all staff and superuser accounts.')
        parser.add_argument('--length', type=int, default=20,
                            help='Password length (default: 20).')

    def handle(self, *args, **options):
        User = get_user_model()
        usernames = options['usernames']
        length = options['length']

        if length < 12:
            raise CommandError('Refusing to generate passwords shorter than 12 characters.')

        if options['all_users']:
            users = list(User.objects.all())
        elif options['staff']:
            users = list(User.objects.filter(is_staff=True))
        elif usernames:
            users = list(User.objects.filter(username__in=usernames))
            found = {u.username for u in users}
            missing = [name for name in usernames if name not in found]
            if missing:
                raise CommandError('No such user(s): ' + ', '.join(missing))
        else:
            raise CommandError(
                'Specify usernames, or use --staff or --all. '
                'Nothing was changed.'
            )

        if not users:
            self.stdout.write(self.style.WARNING('No matching users; nothing to do.'))
            return

        results = []
        for user in users:
            password = generate_password(length)
            user.set_password(password)
            user.save(update_fields=['password'])
            results.append((user.username, password))

        width = max(len(name) for name, _ in results)
        self.stdout.write(
            self.style.SUCCESS('\nRotated passwords (shown once — store them now):\n'))
        for name, password in results:
            self.stdout.write(f'  {name.ljust(width)}  {password}')
        self.stdout.write(self.style.WARNING(
            '\nThese passwords are not stored in plaintext and cannot be retrieved again.\n'
        ))
