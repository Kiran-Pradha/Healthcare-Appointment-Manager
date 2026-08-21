from django.contrib import admin
from .models import DoctorProfile, Leave


class LeaveInline(admin.TabularInline):
    model = Leave
    extra = 1


@admin.register(DoctorProfile)
class DoctorProfileAdmin(admin.ModelAdmin):
    # This IS the "Admin creates and manages doctor profiles" requirement —
    # the Django admin site gives us a full CRUD UI for free, including
    # inline leave-day management, without hand-building an admin frontend.
    list_display = ('user', 'specialisation', 'working_hours_start', 'working_hours_end',
                     'slot_duration_minutes', 'is_active')
    list_filter = ('specialisation', 'is_active')
    search_fields = ('user__first_name', 'user__last_name', 'specialisation')
    inlines = [LeaveInline]


@admin.register(Leave)
class LeaveAdmin(admin.ModelAdmin):
    list_display = ('doctor', 'date', 'reason')
    list_filter = ('date',)
