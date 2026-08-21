from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import CustomLoginForm, PatientRegistrationForm
from .models import User


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    selected_role = request.POST.get('role') or request.GET.get('role')

    if request.method == 'POST':
        form = CustomLoginForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            messages.success(request, "Welcome back!")
            return redirect('dashboard')
    else:
        form = CustomLoginForm(initial={'role': selected_role})

    return render(request, 'accounts/login.html', {
        'form': form,
        'selected_role': selected_role,
    })


def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = PatientRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Welcome! Your account has been created.")
            return redirect('dashboard')
    else:
        form = PatientRegistrationForm()

    return render(request, 'accounts/register.html', {'form': form})


@login_required
def dashboard_router(request):
    """
    Single '/dashboard/' URL that routes to the right template based on
    role, so login/registration redirects don't need to know about roles —
    they just always go to /dashboard/.
    """
    if request.user.role == User.Role.PATIENT:
        return redirect('patient_dashboard')
    elif request.user.role == User.Role.DOCTOR:
        return redirect('doctor_dashboard')
    elif request.user.role == User.Role.ADMIN or request.user.is_staff:
        return redirect('/admin/')
    return redirect('login')
