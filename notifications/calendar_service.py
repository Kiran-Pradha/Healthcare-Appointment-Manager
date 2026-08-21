"""
Google Calendar integration.

Design choice (explained fully in the README's "Google Calendar setup"
section): rather than making every patient go through their own OAuth
consent screen — a lot of integration surface for a 4-day build, and a
worse UX — the CLINIC connects ONE Google account (the admin does this
once). Every appointment becomes an event on that single calendar with the
patient and doctor added as `attendees`, so Google automatically emails
both of them a calendar invite. This satisfies "Google Calendar event
created for both on booking; updated on reschedule; deleted on cancellation"
with a fraction of the integration code a per-user OAuth flow would need.

This module degrades gracefully if Calendar isn't configured: every
function returns None/False instead of raising, and callers already treat
a None event id as "no calendar event to track" — so the app is fully
usable with email-only notifications if Calendar credentials are never set.
"""

import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_calendar_service():
    """
    Returns an authenticated Google Calendar API client, or None if not
    configured / credentials are missing / stored token is invalid.

    Full OAuth setup (one-time, done by the clinic admin) is documented in
    the README. In short: run `python manage.py setup_google_calendar`,
    complete the browser consent flow, and the resulting refresh token is
    stored in `notifications/google_token.json` (gitignored) — from then
    on this function loads and auto-refreshes it silently.
    """
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        return None

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        import json
        import os

        token_path = os.path.join(settings.BASE_DIR, 'notifications', 'google_token.json')
        if not os.path.exists(token_path):
            logger.warning("Google Calendar not connected yet — run `manage.py setup_google_calendar`.")
            return None

        with open(token_path) as f:
            token_data = json.load(f)

        creds = Credentials.from_authorized_user_info(token_data)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, 'w') as f:
                f.write(creds.to_json())

        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        logger.warning("Could not initialize Google Calendar client: %s", e)
        return None


def create_calendar_event(appointment) -> str | None:
    """Creates an event with patient+doctor as attendees. Returns event id, or None on any failure."""
    service = _get_calendar_service()
    if service is None:
        return None

    try:
        from datetime import datetime
        start_dt = datetime.combine(appointment.date, appointment.start_time)
        end_dt = datetime.combine(appointment.date, appointment.end_time)

        event = {
            'summary': f"Appointment: {appointment.patient.get_full_name()} with Dr. {appointment.doctor.user.get_full_name()}",
            'description': f"Specialisation: {appointment.doctor.specialisation}\nBooked via Clinic Appointment Manager.",
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': settings.TIME_ZONE},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': settings.TIME_ZONE},
            'attendees': [
                {'email': appointment.patient.email},
                {'email': appointment.doctor.user.email},
            ],
            'reminders': {'useDefault': True},
        }
        created = service.events().insert(
            calendarId='primary', body=event, sendUpdates='all'
        ).execute()
        return created.get('id')
    except Exception as e:
        logger.warning("Failed to create calendar event for appointment %s: %s", appointment.id, e)
        return None


def update_calendar_event(appointment) -> bool:
    """Updates an existing event's time (used on reschedule)."""
    service = _get_calendar_service()
    if service is None or not appointment.google_calendar_event_id:
        return False
    try:
        from datetime import datetime
        start_dt = datetime.combine(appointment.date, appointment.start_time)
        end_dt = datetime.combine(appointment.date, appointment.end_time)
        service.events().patch(
            calendarId='primary', eventId=appointment.google_calendar_event_id,
            body={
                'start': {'dateTime': start_dt.isoformat(), 'timeZone': settings.TIME_ZONE},
                'end': {'dateTime': end_dt.isoformat(), 'timeZone': settings.TIME_ZONE},
            },
            sendUpdates='all',
        ).execute()
        return True
    except Exception as e:
        logger.warning("Failed to update calendar event %s: %s", appointment.google_calendar_event_id, e)
        return False


def delete_calendar_event(event_id: str) -> bool:
    service = _get_calendar_service()
    if service is None or not event_id:
        return False
    try:
        service.events().delete(calendarId='primary', eventId=event_id, sendUpdates='all').execute()
        return True
    except Exception as e:
        logger.warning("Failed to delete calendar event %s: %s", event_id, e)
        return False
