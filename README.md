# Healthcare Appointment & Follow-up Manager

A clinic appointment platform with separate patient, doctor, and admin experiences: patients search doctors and book slots, submit symptoms and get an AI-generated pre-visit summary for the doctor, doctors submit post-visit notes that become a patient-friendly AI summary, and both sides stay informed via email and Google Calendar.

**Hosted application URL:** https://healthcare-appointment-manager-nc9z.onrender.com

Built with **Django + PostgreSQL** (SQLite for zero-setup local dev), server-rendered templates (no separate frontend build), and the **Anthropic API** for LLM summaries.

---

## 1. Quick Start (local, zero external services)

```bash
git clone <this-repo>
cd appointment_manager
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # defaults work with no external services

python manage.py migrate
python manage.py seed_demo_data # creates demo accounts (see below)
python manage.py runserver
```

Visit `http://localhost:8000/`. By default, emails print to your terminal (console backend) and calendar sync is silently skipped — everything else works fully out of the box.

### Email configuration

For real delivery through SendGrid SMTP, set these values in `.env` (or in the Render environment dashboard):

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.sendgrid.net
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=your-sendgrid-api-key
DEFAULT_FROM_EMAIL=verified-sender@yourdomain.com
EMAIL_TIMEOUT=10
HOSPITAL_NAME=PulseCare Hospital
EMAIL_FROM_NAME=PulseCare Hospital
```

Recipients see the sender as `PulseCare Hospital <verified-sender@yourdomain.com>`. The From address must be verified in SendGrid. Password-reset emails include a Spam/Junk reminder, and the website does too after a reset request.

### Demo accounts (created by `seed_demo_data`)

| Role | Username | Password | Notes |
|---|---|---|---|
| Admin | `admin` | `AdminPass123!` | Use `/admin/` to manage doctors, leave, view notification logs |
| Doctor | `dr_asha` | `DoctorPass123!` | General Medicine |
| Doctor | `dr_vikram` | `DoctorPass123!` | Cardiology |
| Doctor | `dr_meera` | `DoctorPass123!` | Pediatrics |
| Patient | `patient_demo` | `PatientPass123!` | |

### Running tests

```bash
python manage.py test
```

13 tests, including a **real two-thread concurrency test** that races two bookings for the same slot (`appointments/tests.py::test_concurrent_booking_only_one_wins`) and asserts the database never ends up with two scheduled appointments for it, and LLM-failure-handling tests that run with no API key configured to prove graceful degradation.

---

## 2. Architecture

```
core/            Django project settings, URL root
accounts/        Custom User model (role: patient/doctor/admin), auth views
doctors/         DoctorProfile, Leave, admin-managed doctor directory
appointments/    SlotHold, Appointment, booking service layer (the core logic)
notifications/   NotificationLog, email service, Google Calendar service
aiservice/       Isolated LLM calls — pre-visit & post-visit summaries
```

Each app owns one concern. `aiservice` and `notifications` are intentionally isolated from the booking flow so an LLM outage or an email provider outage can never break booking itself — they degrade to logged failures / fallback content instead.

**Frontend:** Django templates + Bootstrap 5 (CDN), not a separate SPA. This was a deliberate scope decision for the time budget — no CORS, no build step, no API-contract drift, while still fully satisfying "frontend" as a requirement. AJAX is used only where it meaningfully improves UX (the slot-hold click).

---

## 3. Database Schema

```
User (accounts)
 ├─ id, username, email, first_name, last_name
 ├─ role: patient | doctor | admin
 └─ phone_number, date_of_birth

DoctorProfile (doctors)
 ├─ user (FK -> User, one-to-one)
 ├─ specialisation, bio
 ├─ working_hours_start, working_hours_end, slot_duration_minutes
 └─ is_active

Leave (doctors)
 ├─ doctor (FK -> DoctorProfile)
 ├─ date, reason
 └─ unique_together(doctor, date)

SlotHold (appointments)
 ├─ doctor (FK), patient (FK -> User)
 ├─ date, start_time
 └─ expires_at            <- short-lived; see "Slot Hold Mechanism" below

Appointment (appointments)
 ├─ doctor (FK), patient (FK)
 ├─ date, start_time, end_time
 ├─ status: scheduled | completed | cancelled | no_show
 ├─ symptoms, ai_urgency_level, ai_chief_complaint, ai_suggested_questions (JSON)
 ├─ ai_pre_visit_raw (JSON), ai_pre_visit_failed (bool)
 ├─ clinical_notes, prescription
 ├─ ai_post_visit_summary, ai_post_visit_failed (bool)
 ├─ google_calendar_event_id
 └─ UniqueConstraint(doctor, date, start_time) WHERE status='scheduled'
     <- the actual double-booking guarantee, see Section 4

NotificationLog (notifications)
 ├─ appointment (FK, nullable), recipient (FK -> User)
 ├─ channel: email | calendar
 ├─ notif_type: booking_confirmation | reminder | medication_reminder |
 │              cancellation | leave_conflict | post_visit_summary
 ├─ status: sent | failed | retrying
 └─ retry_count, error_message
```

---

## 4. System Design Write-up (< 800 words)

### Double-booking prevention

Two layers, doing different jobs. **Application-level:** `appointments/services.py::hold_slot()` and `confirm_booking()` run inside `transaction.atomic()` and use `select_for_update()` to lock the relevant `Appointment`/`SlotHold` rows for the slot being requested. Under Postgres, a second concurrent request for the same slot blocks at the lock, then re-checks availability once it acquires it — so it fails with a clean `SlotUnavailableError` instead of a race-condition bug. **Database-level:** `Appointment.Meta.constraints` defines a **conditional** `UniqueConstraint` on `(doctor, date, start_time)` that only applies `WHERE status='scheduled'`. This is the actual guarantee — even if application logic had a bug, or two server processes each got past their own lock, Postgres physically cannot store two scheduled rows for the same slot; a colliding `INSERT` raises `IntegrityError`, caught in `confirm_booking()` and surfaced as a friendly message. The conditional half of the constraint matters just as much as the unique half: without it, a cancelled appointment would permanently block that slot instead of freeing it. This is proven, not just claimed — `appointments/tests.py::test_concurrent_booking_only_one_wins` fires two real threads at the same slot and asserts exactly one booking survives.

### Slot hold mechanism

A patient clicks a slot before writing their symptoms — losing that slot mid-form after two minutes of typing would be a bad experience the spec explicitly asks to avoid. `hold_slot()` creates a `SlotHold` row with a `SLOT_HOLD_MINUTES` (default 5) expiry the instant a slot is clicked, using the same row-locking as booking so two patients can't both hold the same slot. The symptom-form page shows a live countdown; on submit, `confirm_booking()` re-validates the hold hasn't expired before creating the real `Appointment`. Expired holds aren't deleted immediately — they're simply filtered out everywhere they're queried (`expires_at__gt=now()`), so correctness never depends on a cleanup job running on time; a cleanup pass is a tidiness optimization, not a correctness dependency.

### Doctor leave conflict handling

Rather than only *notifying* affected patients (the minimum the spec asks for), marking a `Leave` triggers a Django `post_save` signal (`doctors/signals.py`) that calls `appointments/services.py::handle_leave_conflicts()`. This **cancels** every `SCHEDULED` appointment for that doctor on that date (leaving them "scheduled" with a doctor who won't show up would be dishonest data) and emails each patient with up to three concrete alternative slots pulled from `available_slots()` over the following week — turning a bad-news notification into an actionable one. Using a signal rather than calling this from the admin view directly means the conflict flow can't be silently bypassed by a future second way of creating a `Leave` (an API endpoint, a bulk import, etc.) — it's structurally guaranteed to fire.

### Notification failure handling

Every notification attempt — not just failures — is written to `NotificationLog` with a status (`sent`/`failed`/`retrying`) and retry count, visible in Django admin. This makes failures **observable** instead of a silent `sendEmail()` call that either worked or didn't with no trace. `notifications/management/commands/retry_failed_notifications.py` is a background job (intended to run every 15–30 min via a free cron trigger on Render/Railway) that retries `failed`/`retrying` entries up to 3 times, incrementing `retry_count` each attempt.

### LLM integration and failure handling

All LLM calls live in the isolated `aiservice` app (see Section 6 for prompts). Every failure mode — timeout, API error, malformed/non-JSON response, missing API key — is caught explicitly (never a blanket `except: pass`) and logged, and every public function returns a plain dict with a `failed` flag instead of ever raising. A deliberate safety-oriented choice: on any pre-visit summary failure, `urgency_level` defaults to **"Medium"**, never "Low" — a failed AI call must never make a genuinely urgent case look calm on the doctor's dashboard. This is proven with tests that run with no API key configured (`aiservice/tests.py`), not just described.

---

## 5. API / URL Reference

| URL | Method | Access | Purpose |
|---|---|---|---|
| `/accounts/register/` | GET/POST | Public | Patient self-registration |
| `/accounts/login/` , `/accounts/logout/` | GET/POST | Public | Auth |
| `/accounts/password-reset/` | GET/POST | Public | Request password-reset email |
| `/accounts/password-reset/<uidb64>/<token>/` | GET/POST | Public | Choose a new password |
| `/` | GET | Logged in | Doctor search/directory |
| `/appointments/doctor/<id>/` | GET | Logged in | Slot picker for one doctor |
| `/appointments/doctor/<id>/hold/` | POST (AJAX) | Patient | Place a slot hold, returns JSON |
| `/appointments/symptoms/<hold_id>/` | GET/POST | Patient | Symptom intake → confirms booking |
| `/appointments/dashboard/patient/` | GET | Patient | Upcoming/past appointments |
| `/appointments/dashboard/doctor/` | GET | Doctor | Today's + upcoming appointments |
| `/appointments/<id>/` | GET/POST | Owner | View AI summaries; doctor submits post-visit notes |
| `/appointments/<id>/cancel/` | POST | Owner | Cancel appointment |
| `/admin/` | — | Admin/staff | Manage doctors, leave, view notification logs |

Doctor profiles and leave days are managed entirely through `/admin/` (Django's built-in admin, extended with an inline leave editor) rather than a hand-built admin UI — this was a deliberate scope decision to spend build time on the harder booking/concurrency logic instead of re-implementing CRUD Django already provides.

---

## 6. LLM Prompts (as implemented in `aiservice/services.py`)

**Pre-visit summary:**
```
Analyse these patient-reported symptoms and respond with ONLY a JSON object
(no markdown fences, no preamble) with exactly these keys:
- "urgency_level": one of "Low", "Medium", "High"
- "chief_complaint": a short (under 15 words) plain-language summary of the main issue
- "suggested_questions": a list of exactly 3 short questions the doctor should consider asking

Symptoms: {symptoms}
```

**Post-visit summary:**
```
Convert these clinical notes and prescription into a warm, plain-language
summary a patient (non-medical background) can understand. Include:
- What the diagnosis / assessment means in simple terms
- A clear medication schedule (drug, dose, how often, for how long)
- Any follow-up steps or warning signs to watch for

Write it as friendly prose, not a bulleted clinical report. Keep it under 200 words.

Clinical notes: {notes}
Prescription: {prescription}
```

Model: `claude-sonnet-4-6` (configurable via `LLM_MODEL` in `.env`).

---

## 7. Google Calendar Setup

**Design choice:** rather than every patient completing their own OAuth consent flow (heavy integration cost, worse UX for a 4-day build), the **clinic connects ONE Google account**. Every appointment becomes a calendar event with the patient and doctor added as `attendees` — Google automatically emails both an invite and keeps their personal calendars in sync. This satisfies "calendar event for both on booking, updated on reschedule, deleted on cancellation" with a fraction of a per-user OAuth flow's code.

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project → enable the **Google Calendar API**.
2. Create OAuth 2.0 credentials (Desktop app type) → note the Client ID and Client Secret.
3. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`.
4. Run the one-time setup command as the clinic admin:
   ```bash
   python manage.py setup_google_calendar
   ```
   This opens a browser consent screen. Log in with the **clinic's** Google account (not a personal one). The resulting token is saved to `notifications/google_token.json` (gitignored) and auto-refreshes silently from then on.
5. That's it — bookings/cancellations now create/delete real calendar events automatically.

**Without this configured, the app is still fully functional** — `calendar_service.py` returns `None`/`False` on every call rather than raising, so booking and email notifications work normally with calendar sync simply skipped.

---

## 8. Background Jobs (cron)

Deploy target (Render/Railway) free-tier cron triggers, or any scheduler, calling:

```bash
python manage.py send_reminders                 # daily: appointment + medication reminders
python manage.py retry_failed_notifications      # every 15-30 min: retry failed emails
```

No Celery/Redis — deliberately, to keep the infrastructure footprint appropriate for a 4-day build and a free-tier deploy. Both commands are idempotent and safe to run repeatedly.

---

## 9. Known Scope Cuts (honest, not hidden)

Given the time budget, these were deliberately left out in favor of depth on the concurrency/reliability requirements the spec weights most heavily:

- Per-user Google OAuth (single clinic-account model used instead — see Section 7)
- Precise per-dose medication reminder timing (daily digest-style reminder instead — see `send_reminders.py` docstring)
- Admin analytics dashboard
- SMS notifications
- Multi-timezone support beyond the single configured `TIME_ZONE`

---

## 10. Deployment (Render / Railway, free tier)

1. Push this repo to GitHub.
2. In Render, choose **New → Blueprint**, select this repository, and apply `render.yaml`. This creates the web service.
3. Set the secret environment variables in the Render dashboard: `SECRET_KEY`, `EMAIL_HOST_PASSWORD`, `ANTHROPIC_API_KEY`, `GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET`. Set `ALLOWED_HOSTS` to include the generated `*.onrender.com` hostname. Also set `DEFAULT_FROM_EMAIL` to a SendGrid-verified address and update `HOSPITAL_NAME` / `EMAIL_FROM_NAME` with the name patients should see.
4. Confirm the generated service URL opens the login page. The Render free tier may suspend an idle service; the first request after suspension can take a little longer.
5. For Railway, create a Django service, set build command `pip install -r requirements.txt && python manage.py migrate`, start command `gunicorn core.wsgi`, attach PostgreSQL, and copy the variables from `.env.example`.
6. Configure the commands in Section 8 as optional scheduled jobs if your hosting plan supports cron jobs.

Do not place credentials in this repository or commit `notifications/google_token.json`.
