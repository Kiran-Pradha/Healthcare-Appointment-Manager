from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    # Adds role/phone to the existing Django UserAdmin instead of replacing
    # it, so we keep permissions/groups management for free.
    fieldsets = UserAdmin.fieldsets + (
        ('Role info', {'fields': ('role', 'phone_number', 'date_of_birth')}),
    )
    list_display = ('username', 'email', 'role', 'is_staff')
    list_filter = ('role', 'is_staff')
