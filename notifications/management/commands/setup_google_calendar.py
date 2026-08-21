"""
One-time interactive OAuth setup, run ONCE by the clinic admin:

    python manage.py setup_google_calendar

Opens a browser consent screen for a single Google account (the "clinic
calendar"). The resulting refresh token is saved to
notifications/google_token.json (gitignored — never commit this file).
From then on, calendar_service.py loads and auto-refreshes it silently on
every booking/cancellation — no further OAuth interaction is needed.

Requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to be set in .env first
(see README "Google Calendar Setup" for how to create these in Google
Cloud Console).
"""

import json
import os
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings


class Command(BaseCommand):
    help = "One-time OAuth setup for the clinic's Google Calendar account."

    def handle(self, *args, **options):
        if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
            raise CommandError(
                "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set in .env. "
                "See README 'Google Calendar Setup' section first."
            )

        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            raise CommandError(
                "google-auth-oauthlib not installed. Run: "
                "pip install google-auth-oauthlib google-api-python-client google-auth"
            )

        client_config = {
            "installed": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
        flow = InstalledAppFlow.from_client_config(
            client_config, scopes=["https://www.googleapis.com/auth/calendar"]
        )

        self.stdout.write("A browser window will open. Log in with the CLINIC's Google account "
                           "(not a personal one) and grant calendar access.")
        creds = flow.run_local_server(port=0)

        token_path = os.path.join(settings.BASE_DIR, 'notifications', 'google_token.json')
        with open(token_path, 'w') as f:
            f.write(creds.to_json())

        self.stdout.write(self.style.SUCCESS(f"Saved credentials to {token_path}. Calendar integration is now active."))
