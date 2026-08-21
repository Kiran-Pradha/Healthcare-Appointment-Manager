from django.contrib.auth import get_user_model
from django.test import TestCase

from .forms import CustomLoginForm


class CustomLoginFormTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='doctoruser',
            email='doctor@example.com',
            password='StrongPass123!',
            role='doctor',
        )

    def test_selected_role_must_match_user_role(self):
        form = CustomLoginForm(data={
            'username': 'doctoruser',
            'password': 'StrongPass123!',
            'role': 'patient',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('role', form.errors)

    def test_valid_role_allows_login(self):
        form = CustomLoginForm(data={
            'username': 'doctoruser',
            'password': 'StrongPass123!',
            'role': 'doctor',
        })

        self.assertTrue(form.is_valid())
