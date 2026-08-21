from django.db import models
from django.conf import settings


class NotificationLog(models.Model):
    """
    A row per notification attempt (email or calendar op). This is the
    answer to "notification failure handling" in the spec: instead of a
    fire-and-forget sendEmail() call with no trace if it silently fails,
    every attempt is recorded here — success, failure, and retry count —
    so failures are visible and retryable rather than invisible.
    """

    class Channel(models.TextChoices):
        EMAIL = 'email', 'Email'
        CALENDAR = 'calendar', 'Google Calendar'

    class NotifType(models.TextChoices):
        BOOKING_CONFIRMATION = 'booking_confirmation', 'Booking confirmation'
        REMINDER = 'reminder', 'Appointment reminder'
        MEDICATION_REMINDER = 'medication_reminder', 'Medication reminder'
        CANCELLATION = 'cancellation', 'Cancellation'
        LEAVE_CONFLICT = 'leave_conflict', 'Leave conflict notice'
        POST_VISIT_SUMMARY = 'post_visit_summary', 'Post-visit summary'

    class Status(models.TextChoices):
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'
        RETRYING = 'retrying', 'Retrying'

    appointment = models.ForeignKey(
        'appointments.Appointment', null=True, blank=True,
        on_delete=models.CASCADE, related_name='notification_logs',
    )
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    channel = models.CharField(max_length=10, choices=Channel.choices)
    notif_type = models.CharField(max_length=30, choices=NotifType.choices)
    status = models.CharField(max_length=10, choices=Status.choices)
    retry_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_attempt_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'notif_type'])]

    def __str__(self):
        return f"{self.notif_type} -> {self.recipient} [{self.status}]"
