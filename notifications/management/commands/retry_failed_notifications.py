"""
Retries FAILED notification log entries, up to MAX_RETRIES each. Intended
to run every 15-30 minutes via a free cron trigger (Render Cron Job /
Railway Cron / GitHub Actions schedule) hitting `python manage.py
retry_failed_notifications`. This is the "background job for ... email
retries" requirement — failures are retried automatically instead of
silently staying failed forever.
"""

from django.core.management.base import BaseCommand
from notifications.models import NotificationLog
from notifications.services import retry_failed


class Command(BaseCommand):
    help = "Retry notifications that previously failed to send."

    def handle(self, *args, **options):
        failed = NotificationLog.objects.filter(
            status__in=[NotificationLog.Status.FAILED, NotificationLog.Status.RETRYING]
        )
        retried, succeeded = 0, 0
        for log_entry in failed:
            retried += 1
            if retry_failed(log_entry):
                succeeded += 1

        self.stdout.write(self.style.SUCCESS(
            f"Retried {retried} failed notification(s), {succeeded} succeeded."
        ))
