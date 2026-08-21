from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError


class DoctorProfile(models.Model):
    """
    One-to-one extension of User for doctor-specific fields.

    Kept separate from User (rather than cramming specialisation/hours onto
    User) because these fields are meaningless for patients/admins, and it
    lets the Admin persona manage this as its own object per the spec
    ("Admin creates and manages doctor profiles").
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='doctor_profile',
        limit_choices_to={'role': 'doctor'},
    )
    specialisation = models.CharField(max_length=120, db_index=True)
    bio = models.TextField(blank=True)

    # Working hours define the booking window each day. Slots are generated
    # on the fly from these rather than pre-materializing every possible
    # slot row forever — see appointments/services.py `available_slots()`.
    working_hours_start = models.TimeField(default='09:00')
    working_hours_end = models.TimeField(default='17:00')
    slot_duration_minutes = models.PositiveIntegerField(default=30)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.working_hours_start >= self.working_hours_end:
            raise ValidationError('Working hours start must be before end.')
        if self.slot_duration_minutes <= 0:
            raise ValidationError('Slot duration must be positive.')

    def __str__(self):
        return f"Dr. {self.user.get_full_name() or self.user.username} — {self.specialisation}"


class Leave(models.Model):
    """
    A single day a doctor is unavailable. Deliberately date-level (not a
    date range field) so conflict-checking stays a simple equality lookup —
    `Appointment.objects.filter(doctor=doctor, date=leave.date)` — instead of
    a range-overlap query, which keeps Step 6 (leave conflict handling)
    simple and fast.
    """

    doctor = models.ForeignKey(DoctorProfile, on_delete=models.CASCADE, related_name='leaves')
    date = models.DateField(db_index=True)
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('doctor', 'date')
        ordering = ['date']

    def __str__(self):
        return f"{self.doctor} on leave {self.date}"
