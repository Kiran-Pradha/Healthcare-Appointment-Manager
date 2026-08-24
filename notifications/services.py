"""
Email notification service. Every send attempt is wrapped and logged to
NotificationLog — this is what makes notification failures *visible and
retryable* instead of silent. A background management command
(notifications/management/commands/retry_failed_notifications.py) later
picks up FAILED rows and retries them, which is the "background job for
... email retries" requirement from the spec.
"""

import logging
from email.utils import formataddr
from django.core.mail import send_mail
from django.conf import settings
from .models import NotificationLog

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


def _from_email():
    return formataddr((settings.EMAIL_FROM_NAME, settings.DEFAULT_FROM_EMAIL))


def _send_and_log(*, appointment, recipient, subject, message, notif_type):
    """
    Core primitive: try to send one email, log the outcome either way.
    Returns True/False for success, but callers generally don't need to
    branch on it — the log is the source of truth for what needs retrying.
    """
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=_from_email(),
            recipient_list=[recipient.email] if recipient.email else [],
            fail_silently=False,
        )
        NotificationLog.objects.create(
            appointment=appointment, recipient=recipient, channel=NotificationLog.Channel.EMAIL,
            notif_type=notif_type, status=NotificationLog.Status.SENT,
        )
        return True
    except Exception as e:
        logger.warning("Email send failed (%s to %s): %s", notif_type, recipient, e)
        NotificationLog.objects.create(
            appointment=appointment, recipient=recipient, channel=NotificationLog.Channel.EMAIL,
            notif_type=notif_type, status=NotificationLog.Status.FAILED, error_message=str(e),
        )
        return False


def retry_failed(log_entry: NotificationLog) -> bool:
    """Re-attempt a previously failed notification. Used by the retry cron job."""
    if log_entry.retry_count >= MAX_RETRIES:
        return False

    appt = log_entry.appointment
    subject, message = _render(log_entry.notif_type, appt)
    try:
        send_mail(
            subject=subject, message=message, from_email=_from_email(),
            recipient_list=[log_entry.recipient.email] if log_entry.recipient.email else [],
            fail_silently=False,
        )
        log_entry.status = NotificationLog.Status.SENT
        log_entry.retry_count += 1
        log_entry.save()
        return True
    except Exception as e:
        log_entry.retry_count += 1
        log_entry.status = NotificationLog.Status.FAILED if log_entry.retry_count >= MAX_RETRIES else NotificationLog.Status.RETRYING
        log_entry.error_message = str(e)
        log_entry.save()
        return False


def _render(notif_type, appointment):
    doctor_name = f"Dr. {appointment.doctor.user.get_full_name()}"
    patient_name = appointment.patient.get_full_name() or appointment.patient.username
    when = f"{appointment.date} at {appointment.start_time.strftime('%I:%M %p')}"

    if notif_type == NotificationLog.NotifType.BOOKING_CONFIRMATION:
        subject = "Appointment Confirmed"
        message = f"Your appointment with {doctor_name} is confirmed for {when}."
    elif notif_type == NotificationLog.NotifType.CANCELLATION:
        subject = "Appointment Cancelled"
        message = f"The appointment with {doctor_name} on {when} has been cancelled."
    elif notif_type == NotificationLog.NotifType.REMINDER:
        subject = "Appointment Reminder"
        message = f"Reminder: you have an appointment with {doctor_name} on {when}."
    elif notif_type == NotificationLog.NotifType.LEAVE_CONFLICT:
        subject = "Your appointment needs to be rescheduled"
        message = f"{doctor_name} is unavailable on {appointment.date}. Please rebook at your earliest convenience."
    elif notif_type == NotificationLog.NotifType.POST_VISIT_SUMMARY:
        subject = "Your Visit Summary"
        message = appointment.ai_post_visit_summary or "Please check your patient portal for your visit summary."
    else:
        subject = "Clinic Notification"
        message = f"Update regarding your appointment with {doctor_name} on {when}."

    return subject, f"Hello {patient_name},\n\n{message}\n\nThank you,\n{settings.HOSPITAL_NAME}"


def notify_booking_confirmed(appointment):
    """Sends confirmation to BOTH patient and doctor, per spec."""
    subject, message = _render(NotificationLog.NotifType.BOOKING_CONFIRMATION, appointment)
    _send_and_log(appointment=appointment, recipient=appointment.patient, subject=subject,
                  message=message, notif_type=NotificationLog.NotifType.BOOKING_CONFIRMATION)

    doctor_message = (
        f"Hello,\n\nNew appointment booked: "
        f"{appointment.patient.get_full_name() or appointment.patient.username} "
        f"on {appointment.date} at {appointment.start_time.strftime('%I:%M %p')}. "
        f"Urgency: {appointment.ai_urgency_level or 'pending'}.\n\n"
        f"Thank you,\n{settings.HOSPITAL_NAME}"
    )
    _send_and_log(appointment=appointment, recipient=appointment.doctor.user, subject="New Appointment Booked",
                  message=doctor_message, notif_type=NotificationLog.NotifType.BOOKING_CONFIRMATION)


def notify_cancellation(appointment):
    subject, message = _render(NotificationLog.NotifType.CANCELLATION, appointment)
    _send_and_log(appointment=appointment, recipient=appointment.patient, subject=subject,
                  message=message, notif_type=NotificationLog.NotifType.CANCELLATION)
    _send_and_log(appointment=appointment, recipient=appointment.doctor.user, subject=subject,
                  message=message, notif_type=NotificationLog.NotifType.CANCELLATION)


def notify_leave_conflict(appointment, alternatives=None):
    """
    alternatives: optional list of (date, time) tuples suggested as rebook
    options, so the patient doesn't have to search from scratch.
    """
    doctor_name = f"Dr. {appointment.doctor.user.get_full_name()}"
    subject = "Your appointment needs to be rescheduled"
    message = (
        f"Hello {appointment.patient.get_full_name() or appointment.patient.username},\n\n"
        f"{doctor_name} is unavailable on {appointment.date} and your appointment "
        f"at {appointment.start_time.strftime('%I:%M %p')} has been cancelled.\n\n"
    )
    if alternatives:
        message += "Here are some nearby available slots with the same doctor:\n"
        for d, t in alternatives:
            message += f"  - {d.strftime('%a, %b %d')} at {t.strftime('%I:%M %p')}\n"
        message += "\nPlease visit the clinic portal to rebook one of these, or any other time that suits you."
    else:
        message += "Please visit the clinic portal to rebook at your earliest convenience."

    message += f"\n\nThank you,\n{settings.HOSPITAL_NAME}"

    _send_and_log(appointment=appointment, recipient=appointment.patient, subject=subject,
                  message=message, notif_type=NotificationLog.NotifType.LEAVE_CONFLICT)


def notify_post_visit_summary(appointment):
    subject, message = _render(NotificationLog.NotifType.POST_VISIT_SUMMARY, appointment)
    _send_and_log(appointment=appointment, recipient=appointment.patient, subject=subject,
                  message=message, notif_type=NotificationLog.NotifType.POST_VISIT_SUMMARY)


def notify_reminder(appointment):
    subject, message = _render(NotificationLog.NotifType.REMINDER, appointment)
    _send_and_log(appointment=appointment, recipient=appointment.patient, subject=subject,
                  message=message, notif_type=NotificationLog.NotifType.REMINDER)
