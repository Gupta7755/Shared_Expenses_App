from django import forms

from splitly.models import User, Group, Expense, Settlement, Currency, CSVImport, PDFImport, SUPPORTED_CURRENCIES

class CustomUserCreationForm(forms.ModelForm):
    full_name = forms.CharField(max_length=150, required=True, label="Full Name",
                                widget=forms.TextInput(attrs={'placeholder': 'Jane Doe', 'class': 'form-control'}))
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={'placeholder': 'jane@example.com', 'class': 'form-control'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'placeholder': 'At least 6 characters', 'class': 'form-control'}), min_length=6)
    phone_number = forms.CharField(max_length=15, required=False, widget=forms.TextInput(attrs={'placeholder': 'Phone number (optional)', 'class': 'form-control'}))
    profile_picture = forms.ImageField(required=False, widget=forms.FileInput(attrs={'class': 'form-control'}))

    class Meta:
        model = User
        fields = ('email', 'full_name', 'phone_number', 'profile_picture')

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get('email')
        if email:
            self.instance.username = email
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.set_password(self.cleaned_data['password'])
        user.phone_number = self.cleaned_data['phone_number']
        user.profile_picture = self.cleaned_data['profile_picture']
        
        # Parse full name
        full_name = self.cleaned_data['full_name'].strip()
        parts = full_name.split(' ', 1)
        user.first_name = parts[0]
        user.last_name = parts[1] if len(parts) > 1 else ''
        
        if commit:
            user.save()
        return user


class UserProfileForm(forms.ModelForm):
    full_name = forms.CharField(max_length=150, required=True, label="Full Name",
                                widget=forms.TextInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={'class': 'form-control'}))
    phone_number = forms.CharField(max_length=15, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    profile_picture = forms.ImageField(required=False, widget=forms.FileInput(attrs={'class': 'form-control'}))
    preferred_currency = forms.ChoiceField(choices=User.preferred_currency.field.choices, 
                                            widget=forms.Select(attrs={'class': 'form-select'}))

    class Meta:
        model = User
        fields = ('email', 'full_name', 'phone_number', 'profile_picture', 'preferred_currency')

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get('email')
        if email:
            self.instance.username = email
        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['full_name'].initial = self.instance.get_full_name()

    def save(self, commit=True):
        user = super().save(commit=False)
        full_name = self.cleaned_data['full_name'].strip()
        parts = full_name.split(' ', 1)
        user.first_name = parts[0]
        user.last_name = parts[1] if len(parts) > 1 else ''
        if commit:
            user.save()
        return user


class GroupForm(forms.ModelForm):
    name = forms.CharField(max_length=255, widget=forms.TextInput(attrs={'placeholder': 'e.g. Weekend Trip', 'class': 'form-control'}))
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'placeholder': 'Group description...', 'class': 'form-control', 'rows': 3}))

    class Meta:
        model = Group
        fields = ('name', 'description')


class ExpenseForm(forms.ModelForm):
    title = forms.CharField(max_length=255, widget=forms.TextInput(attrs={'placeholder': 'e.g. Groceries', 'class': 'form-control'}))
    amount = forms.DecimalField(max_digits=12, decimal_places=2, widget=forms.NumberInput(attrs={'placeholder': '0.00', 'class': 'form-control', 'step': '0.01'}))
    currency = forms.ChoiceField(choices=SUPPORTED_CURRENCIES, widget=forms.Select(attrs={'class': 'form-select'}))
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    category = forms.ChoiceField(choices=Expense.CATEGORY_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}))
    paid_by = forms.ModelChoiceField(queryset=User.objects.none(), widget=forms.Select(attrs={'class': 'form-select'}))
    split_type = forms.ChoiceField(choices=Expense.SPLIT_CHOICES, widget=forms.Select(attrs={'id': 'id_split_type', 'class': 'form-select'}))
    location = forms.CharField(required=False, widget=forms.TextInput(attrs={'placeholder': 'e.g. Apartment, Restaurant Name', 'class': 'form-control'}))
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'placeholder': 'Additional notes...', 'class': 'form-control', 'rows': 3}))
    receipt = forms.ImageField(required=False, widget=forms.FileInput(attrs={'class': 'form-control'}))

    class Meta:
        model = Expense
        fields = ('title', 'amount', 'currency', 'date', 'category', 'paid_by', 'split_type', 'location', 'description', 'receipt')

    def __init__(self, *args, **kwargs):
        cycle = kwargs.pop('cycle', None)
        super().__init__(*args, **kwargs)
        if cycle:
            self.fields['paid_by'].queryset = cycle.members.all()
        else:
            self.fields['paid_by'].queryset = User.objects.all()


# ─── Phase 2 Forms ─────────────────────────────────────────────────────────────

class CSVImportUploadForm(forms.Form):
    """File upload form for CSV expense import."""
    csv_file = forms.FileField(
        label='CSV File',
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.csv'}),
        help_text='Upload a CSV file with columns: title, amount, currency, date, category, paid_by_email, split_type, participants, description, location'
    )

    def clean_csv_file(self):
        f = self.cleaned_data['csv_file']
        if not f.name.lower().endswith('.csv'):
            raise forms.ValidationError('Only .csv files are accepted.')
        if f.size > 5 * 1024 * 1024:  # 5 MB limit
            raise forms.ValidationError('File size must not exceed 5 MB.')
        return f


class PDFImportUploadForm(forms.Form):
    """File upload form for PDF expense import."""
    pdf_file = forms.FileField(
        label='PDF File',
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf'}),
        help_text='Upload a PDF bank statement, receipt collection, or expense report. '
                  'The AI engine will extract expense entries automatically.'
    )

    def clean_pdf_file(self):
        f = self.cleaned_data['pdf_file']
        if not f.name.lower().endswith('.pdf'):
            raise forms.ValidationError('Only .pdf files are accepted.')
        if f.size > 20 * 1024 * 1024:  # 20 MB limit
            raise forms.ValidationError('File size must not exceed 20 MB.')
        return f


class SettlementForm(forms.ModelForm):
    """Form for recording a manual settlement payment."""
    payer = forms.ModelChoiceField(
        queryset=User.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Who Paid'
    )
    receiver = forms.ModelChoiceField(
        queryset=User.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Paid To'
    )
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'})
    )
    currency = forms.ChoiceField(
        choices=SUPPORTED_CURRENCIES,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional note...'})
    )

    class Meta:
        model = Settlement
        fields = ('payer', 'receiver', 'amount', 'currency', 'date', 'description')

    def __init__(self, *args, **kwargs):
        members = kwargs.pop('members', None)
        super().__init__(*args, **kwargs)
        if members is not None:
            qs = User.objects.filter(pk__in=[m.pk for m in members])
            self.fields['payer'].queryset = qs
            self.fields['receiver'].queryset = qs

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('payer') == cleaned.get('receiver'):
            raise forms.ValidationError('Payer and receiver cannot be the same person.')
        if cleaned.get('amount') and cleaned['amount'] <= 0:
            raise forms.ValidationError('Settlement amount must be positive.')
        return cleaned


class CurrencyForm(forms.ModelForm):
    """Admin-accessible form to update a currency's exchange rate."""
    rate_to_inr = forms.DecimalField(
        max_digits=12, decimal_places=6,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'placeholder': '84.000000'}),
        label='Rate to INR',
        help_text='1 unit of this currency equals how many INR?'
    )

    class Meta:
        model = Currency
        fields = ('rate_to_inr',)
