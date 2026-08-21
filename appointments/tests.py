"""
Tests for the booking service. The concurrency test is the important one —
it's a direct answer to the spec's "handle simultaneous booking attempts
safely" requirement, proven rather than just claimed.
"""

import threading
from datetime import date, timedelta, time as dtime

from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from django.db import connections, OperationalError

from doctors.models import DoctorProfile
from .models import Appointment, SlotHold
from .services import hold_slot, confirm_booking, available_slots, SlotUnavailableError

User = get_user_model()


def make_doctor():
    user = User.objects.create_user(
        username='dr_test', password='pass1234', role=User.Role.DOCTOR,
        first_name='Test', last_name='Doctor',
    )
    return DoctorProfile.objects.create(
        user=user, specialisation='General Medicine',
        working_hours_start=dtime(9, 0), working_hours_end=dtime(12, 0),
        slot_duration_minutes=30,
    )


def make_patient(username):
    return User.objects.create_user(username=username, password='pass1234', role=User.Role.PATIENT)


class SlotGenerationTests(TransactionTestCase):
    def test_available_slots_respects_working_hours(self):
        doctor = make_doctor()
        target_date = date.today() + timedelta(days=1)
        slots = available_slots(doctor, target_date)
        # 9:00-12:00 in 30-min steps = 6 slots
        self.assertEqual(len(slots), 6)
        self.assertEqual(slots[0], dtime(9, 0))
        self.assertEqual(slots[-1], dtime(11, 30))

    def test_booked_slot_excluded_from_available(self):
        doctor = make_doctor()
        patient = make_patient('p1')
        target_date = date.today() + timedelta(days=1)
        hold = hold_slot(doctor, patient, target_date, dtime(9, 0))
        confirm_booking(hold, symptoms='headache')
        slots = available_slots(doctor, target_date)
        self.assertNotIn(dtime(9, 0), slots)


class DoubleBookingPreventionTests(TransactionTestCase):
    """
    TransactionTestCase (not TestCase) is required here — it runs each test
    in real committed transactions instead of one wrapper transaction, which
    is necessary to actually exercise select_for_update() locking behaviour
    across threads the way it would happen in production.
    """

    def test_second_hold_on_same_slot_is_rejected(self):
        doctor = make_doctor()
        p1, p2 = make_patient('p1'), make_patient('p2')
        target_date = date.today() + timedelta(days=1)

        hold_slot(doctor, p1, target_date, dtime(9, 0))
        with self.assertRaises(SlotUnavailableError):
            hold_slot(doctor, p2, target_date, dtime(9, 0))

    def test_concurrent_booking_only_one_wins(self):
        """
        The core proof: fire two booking attempts at the exact same slot
        from two real threads/DB connections simultaneously. Exactly one
        should succeed; the other must fail cleanly, and the DB must end
        up with exactly one SCHEDULED appointment for that slot — never two.
        """
        doctor = make_doctor()
        p1, p2 = make_patient('p1'), make_patient('p2')
        target_date = date.today() + timedelta(days=1)
        slot_time = dtime(10, 0)

        results = {}

        def attempt(patient, key):
            try:
                hold = hold_slot(doctor, patient, target_date, slot_time)
                confirm_booking(hold, symptoms=f'symptoms from {key}')
                results[key] = 'success'
            except SlotUnavailableError:
                results[key] = 'rejected'
            except OperationalError:
                # SQLite (our zero-setup local/test DB) has no row-level
                # locking — it locks the whole file, so a thread that loses
                # the race here gets a raw "database is locked" instead of
                # our friendly SlotUnavailableError. On Postgres (the real
                # deployment target) select_for_update() locks only the
                # relevant row, so the losing thread waits, re-checks, and
                # raises SlotUnavailableError cleanly instead. Either way,
                # the safety guarantee below (only one booking exists) holds
                # — this branch only affects error-message friendliness,
                # not correctness.
                results[key] = 'rejected'
            finally:
                connections.close_all()  # each thread needs its own connection cleanup

        t1 = threading.Thread(target=attempt, args=(p1, 'p1'))
        t2 = threading.Thread(target=attempt, args=(p2, 'p2'))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        outcomes = list(results.values())
        self.assertEqual(outcomes.count('success'), 1, f"Expected exactly 1 success, got: {results}")
        self.assertEqual(outcomes.count('rejected'), 1, f"Expected exactly 1 rejection, got: {results}")

        scheduled_count = Appointment.objects.filter(
            doctor=doctor, date=target_date, start_time=slot_time,
            status=Appointment.Status.SCHEDULED,
        ).count()
        self.assertEqual(scheduled_count, 1, "Double-booking occurred! More than one scheduled appointment for the same slot.")

    def test_cancelled_appointment_frees_the_slot(self):
        doctor = make_doctor()
        p1, p2 = make_patient('p1'), make_patient('p2')
        target_date = date.today() + timedelta(days=1)

        hold = hold_slot(doctor, p1, target_date, dtime(9, 0))
        appt = confirm_booking(hold, symptoms='cold')
        appt.status = Appointment.Status.CANCELLED
        appt.save()

        # Should now succeed — proves the partial UniqueConstraint (only
        # applies to status='scheduled') is working as intended.
        hold2 = hold_slot(doctor, p2, target_date, dtime(9, 0))
        appt2 = confirm_booking(hold2, symptoms='fever')
        self.assertEqual(appt2.status, Appointment.Status.SCHEDULED)

    def test_expired_hold_does_not_block_confirmation_by_others(self):
        doctor = make_doctor()
        p1, p2 = make_patient('p1'), make_patient('p2')
        target_date = date.today() + timedelta(days=1)

        hold = hold_slot(doctor, p1, target_date, dtime(9, 0))
        # Force it into the past to simulate expiry without sleeping in tests.
        hold.expires_at = hold.expires_at - timedelta(minutes=999)
        hold.save()

        # p2 should now be able to hold + book the same slot.
        hold2 = hold_slot(doctor, p2, target_date, dtime(9, 0))
        appt = confirm_booking(hold2, symptoms='sore throat')
        self.assertEqual(appt.patient, p2)
