"""Delete chat messages older than a retention window.

Intended for a cron job, e.g. keep 90 days of history:

    0 4 * * * cd /home/micu/Video && .venv/bin/python manage.py purge_messages --days 90

Privacy: this is a hard delete; purged content cannot be recovered.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from chat.models import Message


class Command(BaseCommand):
    help = 'Delete messages older than N days (optionally for a single room).'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, required=True,
                            help='Delete messages older than this many days.')
        parser.add_argument('--room', help='Restrict the purge to one room name.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Only report what would be deleted.')

    def handle(self, *args, **options):
        days = options['days']
        if days < 1:
            raise CommandError('--days must be at least 1.')

        qs = Message.objects.filter(timestamp__lt=timezone.now() - timedelta(days=days))
        if options['room']:
            qs = qs.filter(room__name=options['room'])

        count = qs.count()
        if options['dry_run']:
            self.stdout.write(f'Would delete {count} message(s) older than {days} day(s).')
            return

        qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {count} message(s) older than {days} day(s).'
        ))
