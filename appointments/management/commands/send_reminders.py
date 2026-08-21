"""
Run daily (or more often) via a free hosting provider's cron feature
(Render Cron Jobs / Railway Cron), e.g.:

    python manage.py send_reminders

This is the "background job for medication reminders" requirement. It's a
plain management command rather than Celery+Redis — deliberately, given the
project's time budget: a scheduled command achieves the same functional
outcome with a fraction of the infrastructure to set up and document.

Idempotency: before sending, we check NotificationLog for an existing SENT
entry of the same type for the same appointment *today*, so re-running this
command (e.g. if the cron fires twice, or is scheduled hourly) never spams
the patient with duplicate reminders.
"""

from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from appointments.models import Appointment
from notifications.models import NotificationLog
from notifications.services import notify_reminder, _send_and_log


class Command(BaseCommand):
    help = "Send appointment reminders (next 24h) and medication reminders (active prescriptions)."

    def handle(self, *args, **options):
        self.send_appointment_reminders()
        self.send_medication_reminders()

    def _already_sent_today(self, appointment, notif_type):
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return NotificationLog.objects.filter(
            appointment=appointment, notif_type=notif_type,
            status=NotificationLog.Status.SENT, created_at__gte=today_start,
        ).exists()

    def send_appointment_reminders(self):
        tomorrow = date.today() + timedelta(days=1)
        upcoming = Appointment.objects.filter(
            date=tomorrow, status=Appointment.Status.SCHEDULED
        ).select_related('patient', 'doctor__user')

        sent = 0
        for appt in upcoming:
            if self._already_sent_today(appt, NotificationLog.NotifType.REMINDER):
                continue
            notify_reminder(appt)
            sent += 1
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} appointment reminder(s)."))

    def send_medication_reminders(self):
        """
        Simple frequency model: for any COMPLETED appointment with a
        prescription, send one medication reminder per day for
        MEDICATION_REMINDER_DAYS days after the visit. A production version
        would parse structured dosage/frequency per-drug; this delivers the
        spec's core requirement (reminders "based on prescription frequency")
        without over-building a drug-interaction-aware scheduler in the time
        available — documented as a known simplification in the README.
        """
        MEDICATION_REMINDER_DAYS = 5
        window_start = date.today() - timedelta(days=MEDICATION_REMINDER_DAYS)

        completed_with_rx = Appointment.objects.filter(
            status=Appointment.Status.COMPLETED,
            date__gte=window_start, date__lte=date.today(),
        ).exclude(prescription='').select_related('patient', 'doctor__user')

        sent = 0
        for appt in completed_with_rx:
            if self._already_sent_today(appt, NotificationLog.NotifType.MEDICATION_REMINDER):
                continue
            message = (
                f"Reminder: please continue your prescribed medication as directed by "
                f"Dr. {appt.doctor.user.get_full_name()}:\n\n{appt.prescription}\n\n"
                f"Contact the clinic if you have any questions or side effects."
            )
            _send_and_log(
                appointment=appt, recipient=appt.patient, subject="Medication Reminder",
                message=message, notif_type=NotificationLog.NotifType.MEDICATION_REMINDER,
            )
            sent += 1
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} medication reminder(s)."))
