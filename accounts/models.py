from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Single custom User model with a `role` field, rather than three separate
    login systems. This is what makes role-based auth simple everywhere else:
    one `request.user.role` check instead of juggling different models.

    Django's built-in `is_staff` / `is_superuser` still exist underneath and
    are used for the Django admin site (used by the clinic Admin persona to
    manage doctor profiles, per the spec).
    """

    class Role(models.TextChoices):
        PATIENT = 'patient', 'Patient'
        DOCTOR = 'doctor', 'Doctor'
        ADMIN = 'admin', 'Admin'

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.PATIENT)
    phone_number = models.CharField(max_length=20, blank=True)

    # Extra fields only meaningful for patients, kept here rather than a
    # separate PatientProfile model since the spec doesn't need much beyond
    # this — avoids an unnecessary extra table/join for every booking read.
    date_of_birth = models.DateField(null=True, blank=True)

    def is_patient(self):
        return self.role == self.Role.PATIENT

    def is_doctor(self):
        return self.role == self.Role.DOCTOR

    def is_admin_role(self):
        return self.role == self.Role.ADMIN

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"
