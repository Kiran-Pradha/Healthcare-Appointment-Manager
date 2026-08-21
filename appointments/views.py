from datetime import date, timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from doctors.models import DoctorProfile
from accounts.models import User
from .models import Appointment, SlotHold
from .services import available_slots, hold_slot, confirm_booking, SlotUnavailableError
from aiservice.services import generate_pre_visit_summary, generate_post_visit_summary
from notifications.services import notify_booking_confirmed, notify_cancellation
from notifications.calendar_service import create_calendar_event, delete_calendar_event


def _patient_required(user):
    return user.is_authenticated and user.role == User.Role.PATIENT


def _doctor_required(user):
    return user.is_authenticated and user.role == User.Role.DOCTOR


@login_required
def doctor_detail(request, doctor_id):
    """Shows a date picker + available slots for one doctor, patient-facing."""
    doctor = get_object_or_404(DoctorProfile, id=doctor_id, is_active=True)

    date_str = request.GET.get('date')
    if date_str:
        try:
            selected_date = date.fromisoformat(date_str)
        except ValueError:
            selected_date = date.today() + timedelta(days=1)
    else:
        selected_date = date.today() + timedelta(days=1)

    # Offer the next 7 days as quick-pick tabs.
    upcoming_dates = [date.today() + timedelta(days=i) for i in range(1, 8)]
    slots = available_slots(doctor, selected_date)

    return render(request, 'appointments/doctor_detail.html', {
        'doctor': doctor,
        'selected_date': selected_date,
        'upcoming_dates': upcoming_dates,
        'slots': slots,
    })


@login_required
@require_POST
def hold_slot_view(request, doctor_id):
    """
    AJAX endpoint: patient clicked a slot. Places a hold and returns the
    hold id + expiry so the frontend can show a countdown and move the
    patient to the symptom form. Kept as JSON so the slot grid can stay
    a single page without a full reload — but degrades fine since the
    symptom-form step below re-validates everything server-side anyway.
    """
    if not _patient_required(request.user):
        return JsonResponse({'error': 'Only patients can book appointments.'}, status=403)

    doctor = get_object_or_404(DoctorProfile, id=doctor_id)
    date_str = request.POST.get('date')
    time_str = request.POST.get('time')

    try:
        selected_date = date.fromisoformat(date_str)
        from datetime import time as dtime
        selected_time = dtime.fromisoformat(time_str)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid date/time.'}, status=400)

    try:
        hold = hold_slot(doctor, request.user, selected_date, selected_time)
    except SlotUnavailableError as e:
        return JsonResponse({'error': str(e)}, status=409)

    return JsonResponse({
        'hold_id': hold.id,
        'expires_at': hold.expires_at.isoformat(),
        'redirect_url': f'/appointments/symptoms/{hold.id}/',
    })


@login_required
def symptom_form(request, hold_id):
    """
    The patient's symptom intake page, reached only after a successful hold.
    On submit: confirms the booking (hold -> Appointment), runs the LLM
    pre-visit summary, sends confirmation emails, and creates the calendar
    event — all in one place so a partial failure at any step is visible
    and doesn't leave the patient with a silently broken booking.
    """
    hold = get_object_or_404(SlotHold, id=hold_id, patient=request.user)

    if hold.is_expired():
        messages.error(request, "Your slot hold expired. Please choose a slot again.")
        return redirect('doctor_detail', doctor_id=hold.doctor_id)

    if request.method == 'POST':
        symptoms = request.POST.get('symptoms', '').strip()
        if not symptoms:
            messages.error(request, "Please describe your symptoms before confirming.")
            return render(request, 'appointments/symptom_form.html', {'hold': hold})

        try:
            appointment = confirm_booking(hold, symptoms)
        except SlotUnavailableError as e:
            messages.error(request, str(e))
            return redirect('doctor_detail', doctor_id=hold.doctor_id)

        # --- LLM pre-visit summary (never allowed to break the booking) ---
        result = generate_pre_visit_summary(symptoms)
        appointment.ai_urgency_level = result['urgency_level']
        appointment.ai_chief_complaint = result['chief_complaint']
        appointment.ai_suggested_questions = result['suggested_questions']
        appointment.ai_pre_visit_raw = result['raw']
        appointment.ai_pre_visit_failed = result['failed']
        appointment.save()

        # --- Calendar event (single clinic-account OAuth, see notifications/calendar_service.py) ---
        event_id = create_calendar_event(appointment)
        if event_id:
            appointment.google_calendar_event_id = event_id
            appointment.save(update_fields=['google_calendar_event_id'])

        # --- Email confirmations, logged either way ---
        notify_booking_confirmed(appointment)

        messages.success(request, "Appointment booked! A confirmation has been sent to your email.")
        return redirect('patient_dashboard')

    return render(request, 'appointments/symptom_form.html', {'hold': hold})


@login_required
def patient_dashboard(request):
    if not _patient_required(request.user):
        return redirect('dashboard')

    upcoming = Appointment.objects.filter(
        patient=request.user, status=Appointment.Status.SCHEDULED, date__gte=date.today()
    ).select_related('doctor__user').order_by('date', 'start_time')

    past = Appointment.objects.filter(
        patient=request.user
    ).exclude(status=Appointment.Status.SCHEDULED, date__gte=date.today()) \
     .select_related('doctor__user').order_by('-date', '-start_time')[:20]

    leave_conflicts = Appointment.objects.filter(
        patient=request.user, leave_conflict_pending=True, status=Appointment.Status.CANCELLED,
    ).select_related('doctor__user')

    return render(request, 'appointments/patient_dashboard.html', {
        'upcoming': upcoming, 'past': past, 'leave_conflicts': leave_conflicts,
    })


@login_required
def doctor_dashboard(request):
    if not _doctor_required(request.user):
        return redirect('dashboard')

    doctor = get_object_or_404(DoctorProfile, user=request.user)
    today_appts = Appointment.objects.filter(
        doctor=doctor, date=date.today(), status=Appointment.Status.SCHEDULED
    ).select_related('patient').order_by('start_time')

    upcoming_appts = Appointment.objects.filter(
        doctor=doctor, date__gt=date.today(), status=Appointment.Status.SCHEDULED
    ).select_related('patient').order_by('date', 'start_time')[:20]

    return render(request, 'appointments/doctor_dashboard.html', {
        'doctor': doctor, 'today_appts': today_appts, 'upcoming_appts': upcoming_appts,
    })


@login_required
def appointment_detail(request, appointment_id):
    """
    Doctor's view of a single appointment: shows the AI pre-visit summary,
    and lets the doctor submit post-visit notes + prescription, which
    triggers the patient-friendly LLM summary and reminder scheduling.
    """
    appointment = get_object_or_404(Appointment, id=appointment_id)

    is_doctor_owner = _doctor_required(request.user) and appointment.doctor.user_id == request.user.id
    is_patient_owner = appointment.patient_id == request.user.id
    if not (is_doctor_owner or is_patient_owner or request.user.is_staff):
        messages.error(request, "You don't have access to this appointment.")
        return redirect('dashboard')

    if request.method == 'POST' and is_doctor_owner:
        clinical_notes = request.POST.get('clinical_notes', '').strip()
        prescription = request.POST.get('prescription', '').strip()

        appointment.clinical_notes = clinical_notes
        appointment.prescription = prescription
        appointment.status = Appointment.Status.COMPLETED

        result = generate_post_visit_summary(clinical_notes, prescription)
        appointment.ai_post_visit_summary = result['summary']
        appointment.ai_post_visit_failed = result['failed']
        appointment.save()

        messages.success(request, "Visit completed. Patient-friendly summary sent to the patient.")
        return redirect('doctor_dashboard')

    return render(request, 'appointments/appointment_detail.html', {
        'appointment': appointment, 'is_doctor_owner': is_doctor_owner,
    })


@login_required
@require_POST
def cancel_appointment(request, appointment_id):
    appointment = get_object_or_404(Appointment, id=appointment_id)
    is_patient_owner = appointment.patient_id == request.user.id
    is_doctor_owner = _doctor_required(request.user) and appointment.doctor.user_id == request.user.id

    if not (is_patient_owner or is_doctor_owner or request.user.is_staff):
        messages.error(request, "You don't have permission to cancel this appointment.")
        return redirect('dashboard')

    appointment.status = Appointment.Status.CANCELLED
    appointment.save()

    if appointment.google_calendar_event_id:
        delete_calendar_event(appointment.google_calendar_event_id)

    notify_cancellation(appointment)

    messages.success(request, "Appointment cancelled.")
    return redirect('dashboard')
