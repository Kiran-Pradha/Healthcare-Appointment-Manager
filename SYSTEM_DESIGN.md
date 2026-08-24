# System Design Write-up

*(Standalone copy of README Section 4 — provided separately per the deliverables checklist. ~600 words.)*


### Double-booking prevention

Two layers, doing different jobs. **Application-level:** `appointments/services.py::hold_slot()` and `confirm_booking()` run inside `transaction.atomic()` and use `select_for_update()` to lock the relevant `Appointment`/`SlotHold` rows for the slot being requested. Under Postgres, a second concurrent request for the same slot blocks at the lock, then re-checks availability once it acquires it — so it fails with a clean `SlotUnavailableError` instead of a race-condition bug. **Database-level:** `Appointment.Meta.constraints` defines a **conditional** `UniqueConstraint` on `(doctor, date, start_time)` that only applies `WHERE status='scheduled'`. This is the actual guarantee — even if application logic had a bug, or two server processes each got past their own lock, Postgres physically cannot store two scheduled rows for the same slot; a colliding `INSERT` raises `IntegrityError`, caught in `confirm_booking()` and surfaced as a friendly message. The conditional half of the constraint matters just as much as the unique half: without it, a cancelled appointment would permanently block that slot instead of freeing it. This is proven, not just claimed — `appointments/tests.py::test_concurrent_booking_only_one_wins` fires two real threads at the same slot and asserts exactly one booking survives.

### Slot hold mechanism

A patient clicks a slot before writing their symptoms — losing that slot mid-form after two minutes of typing would be a bad experience the spec explicitly asks to avoid. `hold_slot()` creates a `SlotHold` row with a `SLOT_HOLD_MINUTES` (default 5) expiry the instant a slot is clicked, using the same row-locking as booking so two patients can't both hold the same slot. The symptom-form page shows a live countdown; on submit, `confirm_booking()` re-validates the hold hasn't expired before creating the real `Appointment`. Expired holds aren't deleted immediately — they're simply filtered out everywhere they're queried (`expires_at__gt=now()`), so correctness never depends on a cleanup job running on time; a cleanup pass is a tidiness optimization, not a correctness dependency.

### Doctor leave conflict handling

Rather than only *notifying* affected patients (the minimum the spec asks for), marking a `Leave` triggers a Django `post_save` signal (`doctors/signals.py`) that calls `appointments/services.py::handle_leave_conflicts()`. This **cancels** every `SCHEDULED` appointment for that doctor on that date (leaving them "scheduled" with a doctor who won't show up would be dishonest data) and emails each patient with up to three concrete alternative slots pulled from `available_slots()` over the following week — turning a bad-news notification into an actionable one. Using a signal rather than calling this from the admin view directly means the conflict flow can't be silently bypassed by a future second way of creating a `Leave` (an API endpoint, a bulk import, etc.) — it's structurally guaranteed to fire.

### Notification failure handling

Every notification attempt — not just failures — is written to `NotificationLog` with a status (`sent`/`failed`/`retrying`) and retry count, visible in Django admin. This makes failures **observable** instead of a silent `sendEmail()` call that either worked or didn't with no trace. `notifications/management/commands/retry_failed_notifications.py` is a background job (intended to run every 15–30 min via a free cron trigger on Render/Railway) that retries `failed`/`retrying` entries up to 3 times, incrementing `retry_count` each attempt.

Email delivery uses SendGrid SMTP when `EMAIL_BACKEND` is configured for SMTP. Messages use a configurable display name, for example `PulseCare Hospital <verified-sender@yourdomain.com>`, and include a greeting and hospital sign-off so patients can identify the source. `EMAIL_TIMEOUT` defaults to 10 seconds, preventing a stalled SMTP connection from holding the booking request indefinitely. Password-reset instructions are intentionally generic for account privacy; the completion page and email tell users to check Spam/Junk if the message is not visible.

### LLM integration and failure handling

All LLM calls live in the isolated `aiservice` app (see Section 6 for prompts). Every failure mode — timeout, API error, malformed/non-JSON response, missing API key — is caught explicitly (never a blanket `except: pass`) and logged, and every public function returns a plain dict with a `failed` flag instead of ever raising. A deliberate safety-oriented choice: on any pre-visit summary failure, `urgency_level` defaults to **"Medium"**, never "Low" — a failed AI call must never make a genuinely urgent case look calm on the doctor's dashboard. This is proven with tests that run with no API key configured (`aiservice/tests.py`), not just described.

---
