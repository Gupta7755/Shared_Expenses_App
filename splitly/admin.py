from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from splitly.models import (
    User, Group, Membership, ExpenseCycle, ExpenseCycleMember,
    Expense, ExpenseParticipant, Currency, Settlement,
    CSVImport, PDFImport, ImportLog, PDFReport, AuditLog, Notification
)

# Since User is custom (AbstractUser), register it with customized UserAdmin
@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'phone_number', 'preferred_currency', 'is_staff')
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)
    fieldsets = UserAdmin.fieldsets + (
        ('Custom Profile Info', {'fields': ('phone_number', 'profile_picture', 'preferred_currency')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Custom Profile Info', {'fields': ('phone_number', 'profile_picture', 'preferred_currency')}),
    )

@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'created_at', 'is_archived', 'is_deleted')
    list_filter = ('is_archived', 'is_deleted', 'created_at')
    search_fields = ('name', 'description', 'created_by__email')

@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('group', 'user', 'join_date', 'leave_date', 'status')
    list_filter = ('status', 'join_date')
    search_fields = ('group__name', 'user__email')

@admin.register(ExpenseCycle)
class ExpenseCycleAdmin(admin.ModelAdmin):
    list_display = ('id', 'group', 'start_date', 'end_date', 'status')
    list_filter = ('status', 'start_date')
    search_fields = ('group__name',)

@admin.register(ExpenseCycleMember)
class ExpenseCycleMemberAdmin(admin.ModelAdmin):
    list_display = ('cycle', 'user', 'joined_at')
    search_fields = ('cycle__group__name', 'user__email')

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('title', 'cycle', 'amount', 'currency', 'converted_inr_value', 'date', 'category', 'paid_by', 'is_deleted')
    list_filter = ('category', 'currency', 'is_deleted', 'imported_from_csv', 'imported_from_pdf', 'date')
    search_fields = ('title', 'description', 'paid_by__email', 'cycle__group__name')

@admin.register(ExpenseParticipant)
class ExpenseParticipantAdmin(admin.ModelAdmin):
    list_display = ('expense', 'user', 'amount', 'amount_inr')
    search_fields = ('expense__title', 'user__email')

@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ('currency', 'rate_to_inr', 'updated_at', 'updated_by')
    search_fields = ('currency',)

@admin.register(Settlement)
class SettlementAdmin(admin.ModelAdmin):
    list_display = ('group', 'payer', 'receiver', 'amount', 'currency', 'converted_inr_value', 'date')
    list_filter = ('currency', 'date')
    search_fields = ('group__name', 'payer__email', 'receiver__email')

@admin.register(CSVImport)
class CSVImportAdmin(admin.ModelAdmin):
    list_display = ('file_name', 'group', 'uploaded_by', 'uploaded_at', 'status', 'total_rows', 'imported_rows')
    list_filter = ('status', 'uploaded_at')
    search_fields = ('file_name', 'group__name', 'uploaded_by__email')

@admin.register(PDFImport)
class PDFImportAdmin(admin.ModelAdmin):
    list_display = ('file_name', 'group', 'uploaded_by', 'uploaded_at', 'status', 'total_rows', 'imported_rows')
    list_filter = ('status', 'uploaded_at')
    search_fields = ('file_name', 'group__name', 'uploaded_by__email')

@admin.register(ImportLog)
class ImportLogAdmin(admin.ModelAdmin):
    list_display = ('row_number', 'source', 'import_session', 'pdf_import_session', 'status', 'anomaly_type', 'user_decision')
    list_filter = ('source', 'status', 'anomaly_type', 'user_decision')
    search_fields = ('anomaly_explanation', 'decision_note')

@admin.register(PDFReport)
class PDFReportAdmin(admin.ModelAdmin):
    list_display = ('report_type', 'user', 'group', 'cycle', 'generated_at', 'generated_by')
    list_filter = ('report_type', 'generated_at')
    search_fields = ('user__email', 'group__name')

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'description', 'timestamp', 'ip_address')
    list_filter = ('action', 'timestamp')
    search_fields = ('user__email', 'description')

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'notification_type', 'title', 'is_read', 'created_at')
    list_filter = ('notification_type', 'is_read', 'created_at')
    search_fields = ('user__email', 'title', 'message')
