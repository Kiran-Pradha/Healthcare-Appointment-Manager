from django.urls import path
from . import views

urlpatterns = [
    path('', views.doctor_search, name='doctor_search'),
]
