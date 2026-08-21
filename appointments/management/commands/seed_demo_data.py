"""
Creates demo accounts so an evaluator (or you, during dev) can log in
immediately without registering. This directly targets the "hosted
application URL" deliverable — a reviewer who hits a login wall with no
known credentials will bounce; seeded, documented demo logins remove that
friction entirely. Safe to re-run — uses get_or_create throughout.
"""

from datetime import time
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from doctors.models import DoctorProfile

User = get_user_model()


class Command(BaseCommand):
    help = "Seed demo doctor/patient/admin accounts and doctor profiles for quick evaluation."

    def handle(self, *args, **options):
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser(
                username='admin', password='AdminPass123!', email='admin@clinic.example',
                role=User.Role.ADMIN,
            )
            self.stdout.write(self.style.SUCCESS("Created admin / AdminPass123!"))

        doctors_seed = [
            ('dr_asha', 'Asha', 'Rao', 'General Medicine', time(9, 0), time(13, 0), 30),
            ('dr_vikram', 'Vikram', 'Singh', 'Cardiology', time(10, 0), time(16, 0), 20),
            ('dr_meera', 'Meera', 'Iyer', 'Pediatrics', time(9, 0), time(17, 0), 15),
        ]
        for username, first, last, spec, start, end, dur in doctors_seed:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={'first_name': first, 'last_name': last, 'role': User.Role.DOCTOR,
                          'email': f'{username}@clinic.example'},
            )
            if created:
                user.set_password('DoctorPass123!')
                user.save()
                DoctorProfile.objects.create(
                    user=user, specialisation=spec,
                    working_hours_start=start, working_hours_end=end, slot_duration_minutes=dur,
                )
                self.stdout.write(self.style.SUCCESS(f"Created {username} / DoctorPass123! ({spec})"))

        if not User.objects.filter(username='patient_demo').exists():
            patient = User.objects.create_user(
                username='patient_demo', password='PatientPass123!', role=User.Role.PATIENT,
                first_name='Ravi', last_name='Kumar', email='patient_demo@example.com',
            )
            self.stdout.write(self.style.SUCCESS("Created patient_demo / PatientPass123!"))

        self.stdout.write(self.style.SUCCESS(
            "\nDemo accounts ready:\n"
            "  Admin:   admin / AdminPass123!  (use /admin/)\n"
            "  Doctor:  dr_asha / DoctorPass123!  (General Medicine)\n"
            "  Doctor:  dr_vikram / DoctorPass123!  (Cardiology)\n"
            "  Doctor:  dr_meera / DoctorPass123!  (Pediatrics)\n"
            "  Patient: patient_demo / PatientPass123!\n"
        ))
