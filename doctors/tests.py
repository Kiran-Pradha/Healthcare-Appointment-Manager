from datetime import date, timedelta, time as dtime
from django.test import TestCase
from django.contrib.auth import get_user_model
from doctors.models import DoctorProfile, Leave
from appointments.models import Appointment
from appointments.services import hold_slot, confirm_booking
from notifications.models import NotificationLog

User = get_user_model()


class LeaveConflictTests(TestCase):
    def test_marking_leave_cancels_and_notifies_affected_patients(self):
        doc_user = User.objects.create_user(username='dr_x', password='p', role=User.Role.DOCTOR,
                                              first_name='X', last_name='Y', email='drx@example.com')
        doctor = DoctorProfile.objects.create(
            user=doc_user, specialisation='General', working_hours_start=dtime(9, 0),
            working_hours_end=dtime(12, 0), slot_duration_minutes=30,
        )
        patient = User.objects.create_user(username='p1', password='p', email='p1@example.com')
        target_date = date.today() + timedelta(days=3)

        hold = hold_slot(doctor, patient, target_date, dtime(9, 0))
        appt = confirm_booking(hold, symptoms='cough')
        self.assertEqual(appt.status, Appointment.Status.SCHEDULED)

        # Admin marks the doctor on leave for that date -> signal should fire.
        Leave.objects.create(doctor=doctor, date=target_date, reason='Personal')

        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CANCELLED)

        logs = NotificationLog.objects.filter(
            appointment=appt, notif_type=NotificationLog.NotifType.LEAVE_CONFLICT
        )
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().status, NotificationLog.Status.SENT)

    def test_leave_on_date_with_no_appointments_does_nothing_bad(self):
        doc_user = User.objects.create_user(username='dr_y', password='p', role=User.Role.DOCTOR,
                                              first_name='A', last_name='B', email='dry@example.com')
        doctor = DoctorProfile.objects.create(
            user=doc_user, specialisation='General', working_hours_start=dtime(9, 0),
            working_hours_end=dtime(12, 0), slot_duration_minutes=30,
        )
        target_date = date.today() + timedelta(days=3)
        # Should not raise even with zero affected appointments.
        Leave.objects.create(doctor=doctor, date=target_date, reason='Conference')
        self.assertEqual(NotificationLog.objects.count(), 0)
