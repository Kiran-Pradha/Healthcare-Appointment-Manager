from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Leave


@receiver(post_save, sender=Leave)
def on_leave_created(sender, instance, created, **kwargs):
    """
    Fires the leave-conflict flow the moment a Leave row is saved — whether
    that happens through the Django admin (inline or standalone), a future
    API endpoint, or a management command. Using a signal here (rather than
    calling handle_leave_conflicts() directly from the admin view) means the
    conflict-notification logic can never be accidentally bypassed by a new
    entry point later.

    The import is deliberately local (not at module top) to avoid a circular
    import: appointments/services.py imports doctors.models, so doctors
    importing appointments.services at module load time would create a
    cycle. Importing inside the handler sidesteps that entirely.
    """
    if not created:
        return
    from appointments.services import handle_leave_conflicts
    handle_leave_conflicts(instance)
