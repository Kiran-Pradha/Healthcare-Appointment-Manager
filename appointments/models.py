from django.conf import settings
from django.db import models
from django.utils import timezone
from doctors.models import DoctorProfile


class SlotHold(models.Model):
    """
    A short-lived reservation on a (doctor, date, start_time) slot, created
    the moment a patient clicks a slot — BEFORE they fill the symptom form.

    Why this exists: without it, a patient could spend two minutes writing
    symptoms only to be told "sorry, someone else booked that" — bad UX, and
    it's literally called out in the spec as "slot hold mechanism". It also
    closes a race window: two patients clicking the same slot within the
    same second both get a *hold* attempt, but only one hold can succeed,
    enforced by `services.hold_slot()` using row locking (see services.py).

    Holds expire after settings.SLOT_HOLD_MINUTES. Expired holds are treated
    as if they don't exist (filtered out everywhere they're queried) rather
    than deleted immediately, so we don't need a cron job just to keep the
    system correct — expiry is enforced by simply checking `expires_at`.
    A cleanup command (Step 9) later garbage-collects old rows for tidiness.
    """

    doctor = models.ForeignKey(DoctorProfile, on_delete=models.CASCADE, related_name='slot_holds')
    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='slot_holds')
    date = models.DateField()
    start_time = models.TimeField()
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['doctor', 'date', 'start_time']),
        ]

    def is_expired(self):
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"Hold: {self.doctor} {self.date} {self.start_time} by {self.patient} (exp {self.expires_at})"


class Appointment(models.Model):
    """
    A confirmed booking. The `UniqueConstraint` below is the actual hard
    guarantee against double-booking — it lives at the database level, so
    even if two requests somehow got past the application-level lock (a bug,
    a second server process, whatever), Postgres itself will reject the
    second INSERT. Application-level locking (services.py) is what gives a
    *clean error message* instead of a raw DB exception; the constraint is
    what gives an *actual guarantee*. Both matter, for different reasons.
    """

    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Scheduled'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'
        NO_SHOW = 'no_show', 'No-show'

    class Urgency(models.TextChoices):
        LOW = 'Low', 'Low'
        MEDIUM = 'Medium', 'Medium'
        HIGH = 'High', 'High'

    doctor = models.ForeignKey(DoctorProfile, on_delete=models.CASCADE, related_name='appointments')
    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='appointments')

    date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SCHEDULED)

    # --- Pre-visit (patient-submitted symptoms + LLM output) ---
    symptoms = models.TextField(blank=True)
    ai_urgency_level = models.CharField(max_length=10, choices=Urgency.choices, blank=True)
    ai_chief_complaint = models.CharField(max_length=255, blank=True)
    ai_suggested_questions = models.JSONField(default=list, blank=True)
    ai_pre_visit_raw = models.JSONField(null=True, blank=True)  # full LLM response, for audit/debug
    ai_pre_visit_failed = models.BooleanField(default=False)  # so UI can show a graceful fallback notice

    # --- Post-visit (doctor notes + LLM patient-friendly summary) ---
    clinical_notes = models.TextField(blank=True)
    prescription = models.TextField(blank=True, help_text="One line per medicine, e.g. 'Amoxicillin 500mg — 3x/day — 5 days'")
    ai_post_visit_summary = models.TextField(blank=True)
    ai_post_visit_failed = models.BooleanField(default=False)

    # --- Google Calendar linkage (for update/delete on reschedule/cancel) ---
    google_calendar_event_id = models.CharField(max_length=255, blank=True)

    # --- Leave-conflict tracking (Step 6) ---
    rescheduled_from = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='rescheduled_to_set'
    )
    leave_conflict_pending = models.BooleanField(
        default=False,
        help_text="True if the doctor was marked on leave for this date after booking; patient needs to reschedule.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date', 'start_time']
        constraints = [
            # THE double-booking guarantee. Only applies to SCHEDULED rows,
            # via a partial/conditional unique index — a cancelled
            # appointment must NOT block someone else from taking that slot.
            models.UniqueConstraint(
                fields=['doctor', 'date', 'start_time'],
                condition=models.Q(status='scheduled'),
                name='unique_scheduled_slot_per_doctor',
            )
        ]
        indexes = [
            models.Index(fields=['doctor', 'date']),
            models.Index(fields=['patient', 'date']),
        ]

    def __str__(self):
        return f"{self.patient} with {self.doctor} on {self.date} {self.start_time} ({self.status})"
