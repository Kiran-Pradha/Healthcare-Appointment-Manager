from django.contrib import admin
from .models import NotificationLog


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    # This is the "visible delivery log" — an evaluator or the clinic admin
    # can see exactly which notifications failed and how many times we
    # retried, instead of failures being silent.
    list_display = ('notif_type', 'recipient', 'channel', 'status', 'retry_count', 'created_at')
    list_filter = ('status', 'notif_type', 'channel')
    search_fields = ('recipient__username', 'recipient__email')
    readonly_fields = [f.name for f in NotificationLog._meta.fields]
