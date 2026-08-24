from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from .models import User


class CustomLoginForm(AuthenticationForm):
    role = forms.ChoiceField(
        choices=[('', 'Select your role')] + list(User.Role.choices),
        required=True,
        label='Access as',
        help_text='Choose the role you want to sign in as.'
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        'role_mismatch': 'This account is registered under a different role. Please choose the correct portal.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({
            'placeholder': 'Username',
            'class': 'form-control form-control-lg',
            'required': True,
        })
        self.fields['password'].widget.attrs.update({
            'placeholder': 'Password',
            'class': 'form-control form-control-lg',
            'required': True,
        })
        self.fields['role'].widget.attrs.update({
            'class': 'form-select d-none',
            'required': True,
        })

    def clean(self):
        cleaned_data = super().clean()
        selected_role = cleaned_data.get('role')
        user = self.get_user()

        if user is not None and selected_role:
            if getattr(user, 'role', None) != selected_role:
                self.add_error('role', self.error_messages['role_mismatch'])

        return cleaned_data


class PatientRegistrationForm(UserCreationForm):
    """
    Only patients self-register. Doctors are onboarded by the Admin (per the
    spec: "Admin creates and manages doctor profiles") — letting anyone
    register as a doctor would be a real security hole in a healthcare app,
    so that path is deliberately not exposed here.
    """
    first_name = forms.CharField(max_length=150, required=True)
    last_name = forms.CharField(max_length=150, required=True)
    email = forms.EmailField(required=True)
    phone_number = forms.CharField(max_length=20, required=False)
    date_of_birth = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'phone_number', 'date_of_birth')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.PATIENT
        if commit:
            user.save()
        return user
