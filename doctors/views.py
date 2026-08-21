from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import DoctorProfile


@login_required
def doctor_search(request):
    """
    Patient-facing doctor directory. Filters by specialisation via a simple
    query param rather than a form POST, so the results are shareable/
    bookmarkable URLs and the page works fine without JS.
    """
    query = request.GET.get('specialisation', '').strip()
    doctors = DoctorProfile.objects.filter(is_active=True).select_related('user')
    if query:
        doctors = doctors.filter(specialisation__icontains=query)

    specialisations = (
        DoctorProfile.objects.filter(is_active=True)
        .values_list('specialisation', flat=True).distinct().order_by('specialisation')
    )

    return render(request, 'doctors/search.html', {
        'doctors': doctors,
        'specialisations': specialisations,
        'query': query,
    })
