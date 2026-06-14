from django.urls import path
from splitly import views

app_name = 'splitly'

urlpatterns = [
    # General / Auth
    path('', views.landing, name='landing'),
    path('signup/', views.signup_view, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile_view, name='profile'),
    
    # Dashboard & Balances
    path('dashboard/', views.dashboard, name='dashboard'),
    path('balances/', views.balances_view, name='balances'),
    path('analytics/', views.analytics_view, name='analytics_view'),
    
    # Notifications
    path('notifications/', views.notifications_view, name='notifications_view'),
    path('notifications/mark-read/<int:notif_id>/', views.mark_notification_read, name='mark_notification_read'),
    path('notifications/mark-all-read/', views.mark_all_notifications_read, name='mark_all_notifications_read'),
    path('notifications/clear/', views.clear_all_notifications, name='clear_all_notifications'),
    
    # Admin Dashboard
    path('admin-dashboard/', views.admin_dashboard_view, name='admin_dashboard'),
    path('admin-dashboard/toggle-user/<int:user_id>/', views.admin_toggle_user_status, name='admin_toggle_user_status'),
    path('admin-dashboard/toggle-group-archive/<int:group_id>/', views.admin_toggle_group_archive, name='admin_toggle_group_archive'),
    path('admin-dashboard/toggle-group-delete/<int:group_id>/', views.admin_toggle_group_delete, name='admin_toggle_group_delete'),
    path('admin-dashboard/toggle-expense-delete/<int:expense_id>/', views.admin_toggle_expense_delete, name='admin_toggle_expense_delete'),
    path('admin-dashboard/delete-settlement/<int:settlement_id>/', views.admin_delete_settlement, name='admin_delete_settlement'),
    
    # Groups
    path('groups/', views.group_list, name='group_list'),
    path('groups/create/', views.group_create, name='group_create'),
    path('groups/<int:group_id>/', views.group_detail, name='group_detail'),
    path('groups/<int:group_id>/update/', views.group_update, name='group_update'),
    path('groups/<int:group_id>/archive/', views.group_archive, name='group_archive'),
    path('groups/<int:group_id>/delete/', views.group_delete, name='group_delete'),
    
    # Group Members & Cycles
    path('groups/<int:group_id>/add_member/', views.group_add_member, name='group_add_member'),
    path('groups/<int:group_id>/remove_member/<int:user_id>/', views.group_remove_member, name='group_remove_member'),
    path('groups/<int:group_id>/cycle/<int:cycle_id>/', views.group_cycle_detail, name='group_cycle_detail'),
    
    # Expenses
    path('expenses/', views.expense_list, name='expense_list'),
    path('groups/<int:group_id>/expense/add/', views.expense_create, name='expense_create'),
    path('expenses/<int:expense_id>/', views.expense_detail, name='expense_detail'),
    path('expenses/<int:expense_id>/edit/', views.expense_update, name='expense_update'),
    path('expenses/<int:expense_id>/delete/', views.expense_delete, name='expense_delete'),
    
    # Reports
    path('groups/<int:group_id>/export/csv/', views.export_group_csv, name='export_group_csv'),
    path('groups/<int:group_id>/export/pdf/', views.download_group_pdf, name='download_group_pdf'),
    path('groups/<int:group_id>/cycle/<int:cycle_id>/export/pdf/', views.download_group_pdf, name='download_group_cycle_pdf'),
    path('export/pdf/individual/', views.download_individual_pdf, name='download_individual_pdf'),
    
    # Settlement
    path('groups/<int:group_id>/settle/<int:to_user_id>/', views.settle_debt, name='settle_debt'),
    path('groups/<int:group_id>/settlement/add/', views.settlement_create, name='settlement_create'),
    path('groups/<int:group_id>/settlements/', views.settlement_list, name='settlement_list'),
    
    # Exchange Rates
    path('exchange-rates/', views.exchange_rates, name='exchange_rates'),
    
    # CSV Import
    path('groups/<int:group_id>/csv/upload/', views.csv_import_upload, name='csv_import_upload'),
    path('groups/<int:group_id>/csv/review/<int:session_id>/', views.csv_import_review, name='csv_import_review'),
    path('groups/<int:group_id>/csv/decide/<int:session_id>/', views.csv_import_decide, name='csv_import_decide'),
    path('groups/<int:group_id>/csv/execute/<int:session_id>/', views.csv_import_execute, name='csv_import_execute'),
    path('groups/<int:group_id>/csv/report/<int:session_id>/', views.csv_import_report, name='csv_import_report'),
    
    # Balance Detail
    path('groups/<int:group_id>/balance-detail/', views.balance_detail, name='balance_detail'),
]
