from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone

SUPPORTED_CURRENCIES = [
    ('INR', 'INR (₹)'),
    ('USD', 'USD ($)'),
    ('EUR', 'EUR (€)'),
    ('GBP', 'GBP (£)'),
    ('AED', 'AED (د.إ)'),
]

class User(AbstractUser):
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    profile_picture = models.ImageField(upload_to='profile_pics/', blank=True, null=True)
    preferred_currency = models.CharField(max_length=3, default='INR', choices=SUPPORTED_CURRENCIES)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def save(self, *args, **kwargs):
        if not self.username:
            self.username = self.email
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_full_name() or self.email}"


class Group(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_groups')
    is_archived = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    def get_active_members(self):
        memberships = GroupMembership.objects.filter(group=self, status='active')
        return [m.user for m in memberships]

    @property
    def current_cycle(self):
        return ExpenseCycle.objects.filter(group=self, status='active').order_by('-start_date').first()


class GroupMembership(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='group_memberships')
    join_date = models.DateTimeField(default=timezone.now)
    leave_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')

    class Meta:
        ordering = ['-join_date']

    def __str__(self):
        return f"{self.user.email} in {self.group.name} ({self.status})"


class ExpenseCycle(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('closed', 'Closed'),
    ]
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='cycles')
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    members = models.ManyToManyField(User, through='ExpenseCycleMember', related_name='expense_cycles')

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return f"Cycle {self.id} for {self.group.name} ({self.status})"


class ExpenseCycleMember(models.Model):
    cycle = models.ForeignKey(ExpenseCycle, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    joined_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ('cycle', 'user')

    def __str__(self):
        return f"{self.user.email} in Cycle {self.cycle.id}"


class Expense(models.Model):
    CATEGORY_CHOICES = [
        ('Rent', 'Rent'),
        ('Food', 'Food'),
        ('Travel', 'Travel'),
        ('Shopping', 'Shopping'),
        ('Electricity', 'Electricity'),
        ('Entertainment', 'Entertainment'),
        ('Salary', 'Salary'),
        ('Maintenance', 'Maintenance'),
        ('Medical', 'Medical'),
        ('Other', 'Other'),
    ]
    SPLIT_CHOICES = [
        ('equal', 'Equal Split'),
        ('unequal', 'Unequal Split'),
        ('percentage', 'Percentage Split'),
        ('share', 'Share Split'),
    ]
    cycle = models.ForeignKey(ExpenseCycle, on_delete=models.CASCADE, related_name='expenses')
    title = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR', choices=SUPPORTED_CURRENCIES)
    # Phase 2: currency conversion fields
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=6, default=1.0,
                                         help_text="Rate of 1 unit of currency → INR at time of entry")
    converted_inr_value = models.DecimalField(max_digits=14, decimal_places=2, default=0.0,
                                               help_text="amount × exchange_rate, pre-computed")
    date = models.DateField(default=timezone.now)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='Other')
    paid_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='paid_expenses')
    split_type = models.CharField(max_length=15, choices=SPLIT_CHOICES, default='equal')
    location = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    receipt = models.ImageField(upload_to='receipts/', blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_expenses')
    created_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)
    imported_from_csv = models.BooleanField(default=False)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.title} - {self.currency} {self.amount}"


class ExpenseSplit(models.Model):
    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name='splits')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='splits')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_inr = models.DecimalField(max_digits=14, decimal_places=2, default=0.0,
                                      help_text="Split amount converted to INR")
    input_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f"{self.user.email} owes {self.amount} for {self.expense.title}"


# ── Phase 2: NEW MODELS ────────────────────────────────────────────────────────

class ExchangeRate(models.Model):
    """Stores live/manual exchange rates to INR. Admin-updatable."""
    currency = models.CharField(max_length=3, unique=True, choices=SUPPORTED_CURRENCIES)
    rate_to_inr = models.DecimalField(max_digits=12, decimal_places=6,
                                       help_text="1 unit of this currency = rate_to_inr INR")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"1 {self.currency} = ₹{self.rate_to_inr}"


class Settlement(models.Model):
    """
    Standalone settlement records — completely separate from Expense.
    Represents a direct payment from one member to another to clear a debt.
    """
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='settlements')
    cycle = models.ForeignKey(ExpenseCycle, on_delete=models.CASCADE, related_name='settlements',
                               null=True, blank=True)
    payer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='settlements_paid')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='settlements_received')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR', choices=SUPPORTED_CURRENCIES)
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=6, default=1.0)
    converted_inr_value = models.DecimalField(max_digits=14, decimal_places=2, default=0.0)
    date = models.DateField(default=timezone.now)
    description = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_settlements')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"Settlement: {self.payer} → {self.receiver} {self.currency} {self.amount}"


class CSVImport(models.Model):
    """Tracks each CSV import session."""
    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('reviewing', 'Under Review'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='csv_imports')
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='csv_imports')
    file_name = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    total_rows = models.IntegerField(default=0)
    valid_rows = models.IntegerField(default=0)
    invalid_rows = models.IntegerField(default=0)
    duplicate_rows = models.IntegerField(default=0)
    imported_rows = models.IntegerField(default=0)
    skipped_rows = models.IntegerField(default=0)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"Import '{self.file_name}' for {self.group.name} ({self.status})"


class CSVImportRow(models.Model):
    """Tracks each row from a CSV import session with anomaly info and user decision."""
    STATUS_CHOICES = [
        ('valid', 'Valid'),
        ('anomaly', 'Has Anomaly'),
        ('skipped', 'Skipped'),
        ('imported', 'Imported'),
    ]
    ANOMALY_TYPES = [
        ('DUPLICATE', 'Duplicate Entry'),
        ('NEGATIVE_AMOUNT', 'Negative Amount'),
        ('REFUND', 'Possible Refund'),
        ('SETTLEMENT', 'Possible Settlement'),
        ('INVALID_DATE', 'Invalid Date'),
        ('INVALID_CURRENCY', 'Invalid Currency'),
        ('ZERO_AMOUNT', 'Zero Amount'),
        ('PARTICIPANT_ERROR', 'Participant Not Found'),
        ('SPLIT_TYPE_ERROR', 'Invalid Split Type'),
        ('MISSING_VALUES', 'Missing Required Values'),
        ('MEMBER_CONFLICT', 'Member Join/Leave Conflict'),
        ('NONE', 'No Anomaly'),
    ]
    DECISION_CHOICES = [
        ('pending', 'Pending'),
        ('approve', 'Approved'),
        ('reject', 'Rejected'),
    ]
    import_session = models.ForeignKey(CSVImport, on_delete=models.CASCADE, related_name='rows')
    row_number = models.IntegerField()
    raw_data = models.JSONField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='valid')
    anomaly_type = models.CharField(max_length=25, choices=ANOMALY_TYPES, default='NONE')
    anomaly_explanation = models.TextField(blank=True)
    suggested_action = models.TextField(blank=True)
    user_decision = models.CharField(max_length=10, choices=DECISION_CHOICES, default='pending')
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ['row_number']

    def __str__(self):
        return f"Row {self.row_number} of Import #{self.import_session.id} [{self.status}]"
