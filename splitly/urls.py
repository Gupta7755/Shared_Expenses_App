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
    
    # Settlement
    path('groups/<int:group_id>/settle/<int:to_user_id>/', views.settle_debt, name='settle_debt'),
]
