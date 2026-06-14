import io
from decimal import Decimal
import json
from datetime import datetime, date, timedelta
from collections import defaultdict
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.utils import timezone
from django.db import transaction, models
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.http import HttpResponseForbidden, JsonResponse, FileResponse
from django.views.decorators.http import require_POST

from splitly.models import (
    User, Group, Membership, ExpenseCycle, ExpenseCycleMember,
    Expense, ExpenseParticipant, Settlement, Currency, CSVImport, ImportLog,
    PDFReport, PDFImport, AuditLog, Notification
)
from splitly.forms import (
    CustomUserCreationForm, UserProfileForm, GroupForm, ExpenseForm,
    CSVImportUploadForm, PDFImportUploadForm, SettlementForm, CurrencyForm
)
from splitly.split_engine import calculate_splits, calculate_cycle_balances, simplify_debts, suggest_split_type
from splitly.balance_engine import get_group_balances, get_simple_balances, simplify_debts as simplify
from splitly.currency import convert_to_inr, convert_from_inr, get_rate, get_all_rates, seed_default_rates, format_currency, SUPPORTED_CURRENCIES
from splitly.csv_engine import parse_csv, validate_rows, execute_import, build_report
from splitly.reports import generate_expenses_csv, generate_group_pdf_report, generate_individual_pdf_report
from splitly.pdf_engine import (
    parse_pdf, extract_expenses_from_text, validate_pdf_rows,
    execute_pdf_import, build_pdf_report, ai_suggest_category
)

def landing(request):
    if request.user.is_authenticated:
        return redirect('splitly:dashboard')
    return render(request, 'splitly/landing.html')


def signup_view(request):
    if request.user.is_authenticated:
        return redirect('splitly:dashboard')
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST, request.FILES)
        if form.is_valid():
            user = form.save()
            login(request, user)
            AuditLog.objects.create(
                user=user, 
                action='login', 
                description=f"New user {user.email} signed up and logged in."
            )
            Notification.objects.create(
                user=user,
                notification_type='member_joined',
                title='Welcome to Splitly',
                message='Your profile was created successfully. Create or join a group to start sharing expenses!'
            )
            messages.success(request, "Account created successfully!")
            return redirect('splitly:dashboard')
    else:
        form = CustomUserCreationForm()
    return render(request, 'splitly/signup.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('splitly:dashboard')
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        user = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            AuditLog.objects.create(
                user=user, 
                action='login', 
                description=f"User {user.email} logged in successfully."
            )
            messages.success(request, f"Welcome back, {user.first_name or user.email}!")
            return redirect('splitly:dashboard')
        else:
            messages.error(request, "Invalid email or password.")
    return render(request, 'splitly/login.html')


@login_required
def logout_view(request):
    user = request.user
    logout(request)
    AuditLog.objects.create(
        user=user, 
        action='login', 
        description=f"User {user.email} logged out."
    )
    messages.info(request, "Logged out successfully.")
    return redirect('splitly:landing')


@login_required
def profile_view(request):
    if request.method == 'POST':
        form = UserProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully!")
            return redirect('splitly:profile')
    else:
        form = UserProfileForm(instance=request.user)
    return render(request, 'splitly/profile.html', {'form': form})


@login_required
def dashboard(request):
    user = request.user
    
    # Get all active memberships
    active_memberships = Membership.objects.filter(user=user, status='active', group__is_deleted=False)
    groups = [m.group for m in active_memberships]
    
    # Calculate global You Owe / You Are Owed / Net Balance
    total_owed = Decimal('0.00')
    total_is_owed = Decimal('0.00')
    
    group_balances = []
    
    # Count variables
    total_groups_count = len(groups)
    unique_members = set()
    total_expenses_count = 0
    total_settlements_count = 0
    
    for g in groups:
        cycle = g.current_cycle
        if not cycle:
            continue
            
        # Unique members count
        for m in cycle.members.all():
            unique_members.add(m.id)
            
        # Expenses count
        total_expenses_count += cycle.expenses.filter(is_deleted=False).count()
        # Settlements count
        total_settlements_count += cycle.settlements.count()
        
        _, cycle_balances = calculate_cycle_balances(cycle)
        user_bal = cycle_balances.get(user, Decimal('0.00'))
        
        group_balances.append({
            'group': g,
            'balance': user_bal,
            'abs_balance': abs(user_bal),
            'members_count': cycle.members.count()
        })
        
        if user_bal > 0:
            total_is_owed += user_bal
        elif user_bal < 0:
            total_owed += abs(user_bal)
            
    net_balance = total_is_owed - total_owed
    
    # Get recent activity
    user_cycles = ExpenseCycle.objects.filter(group__in=groups)
    recent_expenses = Expense.objects.filter(
        cycle__in=user_cycles, 
        is_deleted=False
    ).order_by('-date', '-created_at')[:10]
    
    context = {
        'net_balance': net_balance,
        'total_is_owed': total_is_owed,
        'total_owe': total_owed,
        'group_balances': group_balances,
        'recent_activity': recent_expenses,
        
        # New Phase 3 dashboard counters
        'total_groups_count': total_groups_count,
        'total_members_count': len(unique_members),
        'total_expenses_count': total_expenses_count,
        'total_settlements_count': total_settlements_count,
        
        'current_page': 'dashboard'
    }
    return render(request, 'splitly/dashboard.html', context)


@login_required
def group_list(request):
    memberships = Membership.objects.filter(user=request.user, status='active', group__is_deleted=False)
    archived_memberships = Membership.objects.filter(user=request.user, status='active', group__is_archived=True, group__is_deleted=False)
    
    context = {
        'groups': [m.group for m in memberships if not m.group.is_archived],
        'archived_groups': [m.group for m in archived_memberships],
        'current_page': 'groups'
    }
    return render(request, 'splitly/group_list.html', context)


@login_required
@transaction.atomic
def group_create(request):
    if request.method == 'POST':
        form = GroupForm(request.POST)
        if form.is_valid():
            group = form.save(commit=False)
            group.created_by = request.user
            group.save()
            
            # Create membership record for creator
            Membership.objects.create(
                group=group,
                user=request.user,
                status='active'
            )
            
            # Initialize first expense cycle
            cycle = ExpenseCycle.objects.create(
                group=group,
                status='active'
            )
            ExpenseCycleMember.objects.create(
                cycle=cycle,
                user=request.user
            )
            
            AuditLog.objects.create(
                user=request.user,
                action='member_joined',
                description=f"Group '{group.name}' created, user joined as creator."
            )
            
            messages.success(request, f"Group '{group.name}' created successfully!")
            return redirect('splitly:group_detail', group_id=group.id)
    else:
        form = GroupForm()
    return render(request, 'splitly/group_form.html', {'form': form, 'title': 'Create Group'})


@login_required
def group_detail(request, group_id):
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    
    # Check if user is member
    membership = Membership.objects.filter(group=group, user=request.user, status='active').first()
    if not membership:
        return HttpResponseForbidden("You are not a member of this group.")
        
    # Get active cycle
    cycle = group.current_cycle
    if not cycle:
        cycle = ExpenseCycle.objects.create(group=group, status='active')
        for mem in group.get_active_members():
            ExpenseCycleMember.objects.create(cycle=cycle, user=mem)
            
    # Calculate balances for the active cycle
    _, cycle_balances = calculate_cycle_balances(cycle)
    user_balance = cycle_balances.get(request.user, Decimal('0.00'))
    
    # Simplify debts
    transactions = simplify_debts(cycle_balances)
    
    # Get historical cycles
    historical_cycles = group.cycles.filter(status='closed').order_by('-end_date')
    
    # Current cycle expenses
    expenses = cycle.expenses.filter(is_deleted=False)
    
    # All memberships ever for history
    all_memberships = group.memberships.all()
    
    context = {
        'group': group,
        'cycle': cycle,
        'expenses': expenses,
        'user_balance': user_balance,
        'balances': cycle_balances,
        'transactions': transactions,
        'historical_cycles': historical_cycles,
        'memberships': all_memberships,
        'current_page': 'groups'
    }
    return render(request, 'splitly/group_detail.html', context)


@login_required
def group_update(request, group_id):
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    if group.created_by != request.user:
        return HttpResponseForbidden("Only the group creator can edit group details.")
        
    if request.method == 'POST':
        form = GroupForm(request.POST, instance=group)
        if form.is_valid():
            form.save()
            messages.success(request, "Group details updated!")
            return redirect('splitly:group_detail', group_id=group.id)
    else:
        form = GroupForm(instance=group)
    return render(request, 'splitly/group_form.html', {'form': form, 'title': 'Update Group', 'group': group})


@login_required
@transaction.atomic
def group_add_member(request, group_id):
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    # Check if user is member
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    if request.method == 'POST':
        email = request.POST.get('email').strip()
        new_user = User.objects.filter(email=email).first()
        if not new_user:
            messages.error(request, f"No user found with email '{email}'.")
            return redirect('splitly:group_detail', group_id=group.id)
            
        # Check if already active member
        exists = Membership.objects.filter(group=group, user=new_user, status='active').exists()
        if exists:
            messages.warning(request, f"'{new_user}' is already a member of this group.")
            return redirect('splitly:group_detail', group_id=group.id)
            
        # Change membership: old cycle is closed, new cycle started.
        current_cycle = group.current_cycle
        if current_cycle:
            current_cycle.status = 'closed'
            current_cycle.end_date = timezone.now()
            current_cycle.save()
            
        # Add membership history record
        membership = Membership.objects.filter(group=group, user=new_user).first()
        if membership:
            membership.status = 'active'
            membership.join_date = timezone.now()
            membership.leave_date = None
            membership.save()
        else:
            Membership.objects.create(
                group=group,
                user=new_user,
                status='active'
            )
            
        # Create new cycle with all active members
        new_cycle = ExpenseCycle.objects.create(group=group, status='active')
        active_members = group.get_active_members()
        for member in active_members:
            ExpenseCycleMember.objects.create(cycle=new_cycle, user=member)
            
        # Audit & Notification log
        AuditLog.objects.create(
            user=request.user,
            action='member_joined',
            description=f"Member {new_user.email} added to group '{group.name}'."
        )
        for member in active_members:
            if member == new_user:
                Notification.objects.create(
                    user=member,
                    notification_type='member_joined',
                    title='Joined Group',
                    message=f"You have been added to the group '{group.name}' by {request.user.email}."
                )
            else:
                Notification.objects.create(
                    user=member,
                    notification_type='member_joined',
                    title='New Member Joined',
                    message=f"{new_user.email} joined group '{group.name}'."
                )
            
        messages.success(request, f"Added {new_user.get_full_name() or new_user.email} to the group. A new expense cycle has started.")
        
    return redirect('splitly:group_detail', group_id=group.id)


@login_required
@transaction.atomic
def group_remove_member(request, group_id, user_id):
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    # Check if current user is member
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    member_to_remove = get_object_or_404(User, id=user_id)
    
    # Check if target user is in the group
    membership = Membership.objects.filter(group=group, user=member_to_remove, status='active').first()
    if not membership:
        messages.error(request, "User is not an active member of this group.")
        return redirect('splitly:group_detail', group_id=group.id)
        
    # Close old cycle, start new one
    current_cycle = group.current_cycle
    if current_cycle:
        current_cycle.status = 'closed'
        current_cycle.end_date = timezone.now()
        current_cycle.save()
        
    # Mark membership inactive
    membership.status = 'inactive'
    membership.leave_date = timezone.now()
    membership.save()
    
    # Create new cycle with remaining active members
    active_members = group.get_active_members()
    if active_members:
        new_cycle = ExpenseCycle.objects.create(group=group, status='active')
        for member in active_members:
            ExpenseCycleMember.objects.create(cycle=new_cycle, user=member)
            
    # Audit & Notification log
    AuditLog.objects.create(
        user=request.user,
        action='member_left',
        description=f"Member {member_to_remove.email} left/removed from group '{group.name}'."
    )
    Notification.objects.create(
        user=member_to_remove,
        notification_type='member_left',
        title='Removed from Group',
        message=f"You have been removed from group '{group.name}' by {request.user.email}."
    )
    for member in active_members:
        Notification.objects.create(
            user=member,
            notification_type='member_left',
            title='Member Left Group',
            message=f"{member_to_remove.email} left group '{group.name}'."
        )
            
    messages.success(request, f"Removed {member_to_remove.get_full_name() or member_to_remove.email} from the group. A new expense cycle has started.")
    return redirect('splitly:group_detail', group_id=group.id)


@login_required
def group_archive(request, group_id):
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    if group.created_by != request.user:
        return HttpResponseForbidden("Only the group creator can archive the group.")
        
    group.is_archived = not group.is_archived
    group.save()
    status_text = "archived" if group.is_archived else "restored"
    messages.success(request, f"Group '{group.name}' has been {status_text}.")
    return redirect('splitly:group_detail', group_id=group.id)


@login_required
def group_delete(request, group_id):
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    if group.created_by != request.user:
        return HttpResponseForbidden("Only the group creator can delete the group.")
        
    group.is_deleted = True
    group.save()
    messages.success(request, f"Group '{group.name}' has been deleted.")
    return redirect('splitly:group_list')


@login_required
def group_cycle_detail(request, group_id, cycle_id):
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    # Check membership
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    cycle = get_object_or_404(ExpenseCycle, id=cycle_id, group=group)
    
    # Calculate balances for this specific historical cycle
    _, cycle_balances = calculate_cycle_balances(cycle)
    user_balance = cycle_balances.get(request.user, Decimal('0.00'))
    transactions = simplify_debts(cycle_balances)
    expenses = cycle.expenses.filter(is_deleted=False)
    
    context = {
        'group': group,
        'cycle': cycle,
        'expenses': expenses,
        'user_balance': user_balance,
        'balances': cycle_balances,
        'transactions': transactions,
        'historical': True,
        'current_page': 'groups'
    }
    return render(request, 'splitly/group_detail.html', context)


@login_required
@transaction.atomic
def expense_create(request, group_id):
    group = get_object_or_404(Group, id=group_id, is_deleted=False, is_archived=False)
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    cycle = group.current_cycle
    if not cycle:
        return redirect('splitly:group_detail', group_id=group.id)
        
    cycle_members = list(cycle.members.all())
    
    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES, cycle=cycle)
        if form.is_valid():
            expense = form.save(commit=False)
            expense.cycle = cycle
            expense.created_by = request.user
            
            # Fetch dynamic splits parameters
            split_type = form.cleaned_data['split_type']
            inputs = {}
            for member in cycle_members:
                val = request.POST.get(f'split_val_{member.id}', '0')
                inputs[member.id] = val
                
            try:
                # Run split calculations and validation
                splits, breakdown = calculate_splits(expense.amount, split_type, cycle_members, inputs)
                expense.description = (expense.description or '') + f"\n\n--- Calculation Breakdown ---\n{breakdown}"
                
                # Fetch Currency Conversion rates
                rate = get_rate(expense.currency)
                expense.exchange_rate = rate
                expense.converted_inr_value = convert_to_inr(expense.amount, expense.currency)
                expense.save()
                
                # Save split records
                for member, split_amount in splits.items():
                    split_inr = convert_to_inr(split_amount, expense.currency)
                    ExpenseParticipant.objects.create(
                        expense=expense,
                        user=member,
                        amount=split_amount,
                        amount_inr=split_inr,
                        input_value=Decimal(str(inputs.get(member.id, 0)))
                    )
                
                # Audit log & notifications
                AuditLog.objects.create(
                    user=request.user,
                    action='expense_added',
                    description=f"Expense '{expense.title}' of {expense.currency} {expense.amount} added in group '{group.name}'."
                )
                for member in cycle_members:
                    if member != request.user:
                        Notification.objects.create(
                            user=member,
                            notification_type='expense_added',
                            title='New Expense Added',
                            message=f"Expense '{expense.title}' of {expense.currency} {expense.amount} was added to '{group.name}' by {request.user.email}."
                        )
                
                messages.success(request, f"Expense '{expense.title}' recorded successfully!")
                return redirect('splitly:group_detail', group_id=group.id)
                
            except ValidationError as e:
                messages.error(request, f"Split Calculation Error: {e.message if hasattr(e, 'message') else str(e)}")
    else:
        form = ExpenseForm(cycle=cycle, initial={'date': timezone.now().date(), 'currency': request.user.preferred_currency})
        
    context = {
        'form': form,
        'group': group,
        'cycle': cycle,
        'members': cycle_members,
        'title': 'Add Expense'
    }
    return render(request, 'splitly/expense_form.html', context)


@login_required
@transaction.atomic
def expense_update(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id, is_deleted=False)
    cycle = expense.cycle
    group = cycle.group
    
    # Check membership
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    cycle_members = list(cycle.members.all())
    
    # Load existing inputs for pre-population in form template
    existing_splits = {split.user_id: split.input_value for split in expense.splits.all()}
    
    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES, instance=expense, cycle=cycle)
        if form.is_valid():
            updated_expense = form.save(commit=False)
            split_type = form.cleaned_data['split_type']
            
            inputs = {}
            for member in cycle_members:
                val = request.POST.get(f'split_val_{member.id}', '0')
                inputs[member.id] = val
                
            try:
                # Re-calculate splits
                splits, breakdown = calculate_splits(updated_expense.amount, split_type, cycle_members, inputs)
                
                # Strip out old breakdown note
                cleaned_desc = updated_expense.description or ''
                if "--- Calculation Breakdown ---" in cleaned_desc:
                    cleaned_desc = cleaned_desc.split("--- Calculation Breakdown ---")[0].strip()
                    
                updated_expense.description = cleaned_desc + f"\n\n--- Calculation Breakdown ---\n{breakdown}"
                
                # Recalculate converted value
                rate = get_rate(updated_expense.currency)
                updated_expense.exchange_rate = rate
                updated_expense.converted_inr_value = convert_to_inr(updated_expense.amount, updated_expense.currency)
                updated_expense.save()
                
                # Remove old splits and create new ones
                expense.splits.all().delete()
                for member, split_amount in splits.items():
                    split_inr = convert_to_inr(split_amount, updated_expense.currency)
                    ExpenseParticipant.objects.create(
                        expense=updated_expense,
                        user=member,
                        amount=split_amount,
                        amount_inr=split_inr,
                        input_value=Decimal(str(inputs.get(member.id, 0)))
                    )
                    
                AuditLog.objects.create(
                    user=request.user,
                    action='expense_updated',
                    description=f"Expense '{updated_expense.title}' updated in group '{group.name}'."
                )
                    
                messages.success(request, "Expense updated successfully!")
                return redirect('splitly:group_detail', group_id=group.id)
                
            except ValidationError as e:
                messages.error(request, f"Split Calculation Error: {e.message if hasattr(e, 'message') else str(e)}")
    else:
        form = ExpenseForm(instance=expense, cycle=cycle)
        
    context = {
        'form': form,
        'group': group,
        'cycle': cycle,
        'members': cycle_members,
        'existing_splits': existing_splits,
        'title': 'Edit Expense',
        'expense': expense
    }
    return render(request, 'splitly/expense_form.html', context)


@login_required
def expense_delete(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id, is_deleted=False)
    group = expense.cycle.group
    
    # Check membership
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    expense.is_deleted = True
    expense.save()
    
    AuditLog.objects.create(
        user=request.user,
        action='expense_deleted',
        description=f"Expense '{expense.title}' deleted from group '{group.name}'."
    )
    
    messages.success(request, f"Expense '{expense.title}' has been deleted.")
    return redirect('splitly:group_detail', group_id=group.id)


@login_required
def expense_detail(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id, is_deleted=False)
    group = expense.cycle.group
    
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    splits = expense.splits.all()
    
    context = {
        'expense': expense,
        'splits': splits,
        'group': group,
        'current_page': 'groups'
    }
    return render(request, 'splitly/expense_detail.html', context)


@login_required
def expense_list(request):
    memberships = Membership.objects.filter(user=request.user, status='active', group__is_deleted=False)
    groups = [m.group for m in memberships]
    cycles = ExpenseCycle.objects.filter(group__in=groups)
    
    query = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    group_filter = request.GET.get('group', '').strip()
    
    expenses = Expense.objects.filter(cycle__in=cycles, is_deleted=False)
    
    if query:
        expenses = expenses.filter(title__icontains=query)
    if category:
        expenses = expenses.filter(category=category)
    if group_filter:
        expenses = expenses.filter(cycle__group_id=group_filter)
        
    context = {
        'expenses': expenses.order_by('-date', '-created_at'),
        'groups': groups,
        'categories': [c[0] for c in Expense.CATEGORY_CHOICES],
        'q': query,
        'selected_category': category,
        'selected_group': group_filter,
        'current_page': 'expenses'
    }
    return render(request, 'splitly/expense_list.html', context)


@login_required
def export_group_csv(request, group_id):
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    cycles = group.cycles.all()
    expenses = Expense.objects.filter(cycle__in=cycles, is_deleted=False)
    return generate_expenses_csv(expenses)


@login_required
def download_group_pdf(request, group_id, cycle_id=None):
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    if cycle_id:
        cycle = get_object_or_404(ExpenseCycle, id=cycle_id, group=group)
    else:
        cycle = group.current_cycle
        
    if not cycle:
        messages.error(request, "No active cycle found for this group.")
        return redirect('splitly:group_detail', group_id=group.id)
        
    _, cycle_balances = calculate_cycle_balances(cycle)
    transactions = simplify_debts(cycle_balances)
    import_sessions = CSVImport.objects.filter(group=group)
    expenses = cycle.expenses.filter(is_deleted=False)
    settlements_list = cycle.settlements.all()
    
    # Generate PDF Content
    pdf_data = generate_group_pdf_report(
        group, cycle, cycle_balances, transactions, 
        import_sessions, expenses, settlements_list
    )
    
    # Save PDF report record to DB
    filename = f"group_report_{group.id}_{cycle.id}_{timezone.now().strftime('%Y%m%d%H%M%S')}.pdf"
    report_obj = PDFReport.objects.create(
        report_type='group',
        group=group,
        cycle=cycle,
        generated_by=request.user,
    )
    report_obj.file.save(filename, ContentFile(pdf_data))
    report_obj.save()
    
    # Log Audit
    AuditLog.objects.create(
        user=request.user,
        action='pdf_generated',
        description=f"Group PDF report generated for group '{group.name}', cycle #{cycle.id}."
    )
    
    # Notify
    Notification.objects.create(
        user=request.user,
        notification_type='report_generated',
        title='Group PDF Generated',
        message=f"Group PDF report for '{group.name}' has been generated and saved."
    )
    
    # Return as FileResponse
    buffer = io.BytesIO(pdf_data)
    return FileResponse(buffer, as_attachment=True, filename=filename)


@login_required
def download_individual_pdf(request):
    user = request.user
    
    # Memberships
    memberships = Membership.objects.filter(user=user)
    groups = [m.group for m in memberships if not m.group.is_deleted]
    
    # Expense splits involved in
    expenses_involved = ExpenseParticipant.objects.filter(user=user, expense__is_deleted=False)
    
    # Payments made by user
    payments = Expense.objects.filter(paid_by=user, is_deleted=False)
    
    # Settlements user is involved in
    settlements = Settlement.objects.filter(
        models.Q(payer=user) | models.Q(receiver=user)
    ).order_by('-date')
    
    # Categories spend aggregation (in INR)
    categories_summary = {}
    total_shared_inr = Decimal('0.00')
    total_paid_inr = Decimal('0.00')
    settlements_sent_inr = Decimal('0.00')
    settlements_received_inr = Decimal('0.00')
    
    for split in expenses_involved:
        total_shared_inr += split.amount_inr
        categories_summary[split.expense.category] = categories_summary.get(split.expense.category, Decimal('0.00')) + split.amount_inr
        
    for pay in payments:
        total_paid_inr += pay.converted_inr_value
        
    for s in settlements:
        if s.payer == user:
            settlements_sent_inr += s.converted_inr_value
        else:
            settlements_received_inr += s.converted_inr_value
            
    # Calculate percentage
    cat_summary_dict = {}
    if total_shared_inr > 0:
        for cat, val in categories_summary.items():
            cat_summary_dict[cat] = {
                'amount': val,
                'percentage': (val / total_shared_inr) * 100
            }
            
    net_balance = total_paid_inr - total_shared_inr + settlements_sent_inr - settlements_received_inr
    
    overall_stats = {
        'total_paid': total_paid_inr,
        'total_shared': total_shared_inr,
        'settlements_sent': settlements_sent_inr,
        'settlements_received': settlements_received_inr,
        'net_balance': net_balance
    }
    
    # Generate PDF Content
    pdf_data = generate_individual_pdf_report(
        user, memberships, expenses_involved, 
        payments, settlements, cat_summary_dict, overall_stats
    )
    
    # Save PDF report record to DB
    filename = f"individual_report_{user.id}_{timezone.now().strftime('%Y%m%d%H%M%S')}.pdf"
    report_obj = PDFReport.objects.create(
        report_type='individual',
        user=user,
        generated_by=request.user,
    )
    report_obj.file.save(filename, ContentFile(pdf_data))
    report_obj.save()
    
    # Log Audit
    AuditLog.objects.create(
        user=request.user,
        action='pdf_generated',
        description=f"Individual PDF report generated for {user.email}."
    )
    
    # Notify
    Notification.objects.create(
        user=request.user,
        notification_type='report_generated',
        title='Individual PDF Generated',
        message="Your individual PDF report has been generated and saved."
    )
    
    # Return as FileResponse
    buffer = io.BytesIO(pdf_data)
    return FileResponse(buffer, as_attachment=True, filename=filename)


@login_required
def balances_view(request):
    memberships = Membership.objects.filter(user=request.user, status='active', group__is_deleted=False)
    groups = [m.group for m in memberships]
    
    all_debts = []
    
    for g in groups:
        cycle = g.current_cycle
        if not cycle:
            continue
        _, cycle_balances = calculate_cycle_balances(cycle)
        transactions = simplify_debts(cycle_balances)
        
        # Filter transactions involving the current user
        for tx in transactions:
            if tx['from_user'] == request.user or tx['to_user'] == request.user:
                all_debts.append({
                    'group': g,
                    'from_user': tx['from_user'],
                    'to_user': tx['to_user'],
                    'amount': tx['amount']
                })
                
    context = {
        'debts': all_debts,
        'current_page': 'balances'
    }
    return render(request, 'splitly/balances.html', context)


@login_required
@transaction.atomic
def settle_debt(request, group_id, to_user_id):
    group = get_object_or_404(Group, id=group_id, is_deleted=False, is_archived=False)
    cycle = group.current_cycle
    if not cycle:
        return redirect('splitly:group_detail', group_id=group.id)
        
    # Check if both are members of the cycle
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    to_user = get_object_or_404(User, id=to_user_id)
    if not cycle.members.filter(id=to_user.id).exists():
        messages.error(request, "Target user is not in the current expense cycle.")
        return redirect('splitly:group_detail', group_id=group.id)
        
    if request.method == 'POST':
        amount = Decimal(request.POST.get('amount', '0'))
        if amount <= 0:
            messages.error(request, "Settlement amount must be greater than zero.")
            return redirect('splitly:group_detail', group_id=group.id)
            
        title = f"Settlement: {request.user.first_name or request.user.email} paid {to_user.first_name or to_user.email}"
        
        # Create settlement expense
        expense = Expense.objects.create(
            cycle=cycle,
            title=title,
            amount=amount,
            currency=request.user.preferred_currency,
            exchange_rate=get_rate(request.user.preferred_currency),
            converted_inr_value=convert_to_inr(amount, request.user.preferred_currency),
            date=timezone.now().date(),
            category='Other',
            paid_by=request.user,
            split_type='unequal',
            description=f"Direct settlement payment between {request.user} and {to_user}.",
            created_by=request.user
        )
        
        # Target user owes the whole amount
        ExpenseParticipant.objects.create(
            expense=expense,
            user=to_user,
            amount=amount,
            amount_inr=convert_to_inr(amount, expense.currency),
            input_value=amount
        )
        
        # Payer owes 0.00
        ExpenseParticipant.objects.create(
            expense=expense,
            user=request.user,
            amount=Decimal('0.00'),
            amount_inr=Decimal('0.00'),
            input_value=Decimal('0.00')
        )
        
        # Zero out other members in cycle splits
        for member in cycle.members.all():
            if member != request.user and member != to_user:
                ExpenseParticipant.objects.create(
                    expense=expense,
                    user=member,
                    amount=Decimal('0.00'),
                    amount_inr=Decimal('0.00'),
                    input_value=Decimal('0.00')
                )
                
        # Also create a core Settlement record
        s = Settlement.objects.create(
            group=group,
            cycle=cycle,
            payer=request.user,
            receiver=to_user,
            amount=amount,
            currency=request.user.preferred_currency,
            exchange_rate=expense.exchange_rate,
            converted_inr_value=expense.converted_inr_value,
            date=timezone.now().date(),
            description=f"Debt settlement: {request.user.email} paid {to_user.email}",
            created_by=request.user
        )
        
        # Create logs and notifications
        AuditLog.objects.create(
            user=request.user,
            action='settlement_added',
            description=f"Settlement: {request.user.email} paid {to_user.email} {s.currency} {s.amount}."
        )
        Notification.objects.create(
            user=to_user,
            notification_type='settlement_completed',
            title='Settlement Received',
            message=f"{request.user.email} paid you {s.currency} {s.amount} in group '{group.name}'."
        )
                
        messages.success(request, f"Settlement of {amount} to {to_user.get_full_name() or to_user.email} recorded!")
        
    return redirect('splitly:group_detail', group_id=group.id)


# ── CSV Import ─────────────────────────────────────────────────────────────────

@login_required
def csv_import_upload(request, group_id):
    """Step 1: Upload a CSV file for a group."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False, is_archived=False)
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden('Access denied.')

    if request.method == 'POST':
        form = CSVImportUploadForm(request.POST, request.FILES)
        if form.is_valid():
            csv_file = form.cleaned_data['csv_file']
            try:
                raw_rows = parse_csv(csv_file)
            except ValueError as e:
                messages.error(request, str(e))
                return render(request, 'splitly/csv_import_upload.html', {'form': form, 'group': group})

            # Create session record
            session = CSVImport.objects.create(
                group=group,
                uploaded_by=request.user,
                file_name=csv_file.name,
                status='pending',
            )

            # Validate + annotate rows
            validate_rows(session, raw_rows, group)

            messages.info(
                request,
                f"CSV uploaded: {session.total_rows} rows found. "
                f"{session.valid_rows} valid, {session.invalid_rows} need review."
            )
            return redirect('splitly:csv_import_review', group_id=group.id, session_id=session.id)
    else:
        form = CSVImportUploadForm()

    # Past import sessions for this group
    past_imports = CSVImport.objects.filter(group=group, uploaded_by=request.user).order_by('-uploaded_at')[:10]
    return render(request, 'splitly/csv_import_upload.html', {
        'form': form,
        'group': group,
        'past_imports': past_imports,
        'current_page': 'groups',
    })


@login_required
def csv_import_review(request, group_id, session_id):
    """Step 2: Display all rows for review. Shows anomalies with explanations."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    session = get_object_or_404(CSVImport, id=session_id, group=group)

    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden('Access denied.')

    if session.status == 'completed':
        return redirect('splitly:csv_import_report', group_id=group.id, session_id=session.id)

    rows = session.rows.all()
    anomaly_rows = rows.filter(status='anomaly')
    valid_rows = rows.filter(status='valid')
    pending_decisions = anomaly_rows.filter(user_decision='pending').count()

    return render(request, 'splitly/csv_import_review.html', {
        'group': group,
        'session': session,
        'valid_rows': valid_rows,
        'anomaly_rows': anomaly_rows,
        'pending_decisions': pending_decisions,
        'current_page': 'groups',
    })


@login_required
@require_POST
def csv_import_decide(request, group_id, session_id):
    """AJAX / POST endpoint: record approve/reject decision for a single row."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    session = get_object_or_404(CSVImport, id=session_id, group=group)

    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return JsonResponse({'error': 'Access denied'}, status=403)

    row_id = request.POST.get('row_id')
    decision = request.POST.get('decision')  # 'approve' or 'reject'
    note = request.POST.get('note', '')

    if decision not in ('approve', 'reject'):
        return JsonResponse({'error': 'Invalid decision'}, status=400)

    try:
        row = ImportLog.objects.get(id=row_id, import_session=session)
        row.user_decision = decision
        row.decision_note = note
        row.save()
        pending = session.rows.filter(status='anomaly', user_decision='pending').count()
        return JsonResponse({'ok': True, 'pending': pending})
    except ImportLog.DoesNotExist:
        return JsonResponse({'error': 'Row not found'}, status=404)


@login_required
@require_POST
def csv_import_execute(request, group_id, session_id):
    """Step 3: Execute the import — processes all approved rows."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    session = get_object_or_404(CSVImport, id=session_id, group=group)

    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden('Access denied.')

    if session.status == 'completed':
        messages.warning(request, 'This import has already been executed.')
        return redirect('splitly:csv_import_report', group_id=group.id, session_id=session.id)

    # Check all anomalies have decisions
    pending = session.rows.filter(status='anomaly', user_decision='pending').count()
    if pending > 0:
        messages.error(request, f'{pending} anomalous row(s) still need a decision before executing.')
        return redirect('splitly:csv_import_review', group_id=group.id, session_id=session.id)

    try:
        summary = execute_import(session, request.user)
        
        # Log Audit & Notifications
        AuditLog.objects.create(
            user=request.user,
            action='csv_imported',
            description=f"CSV file '{session.file_name}' imported to group '{group.name}': {summary['imported']} items imported."
        )
        active_members = group.get_active_members()
        for member in active_members:
            if member != request.user:
                Notification.objects.create(
                    user=member,
                    notification_type='csv_imported',
                    title='CSV Import Completed',
                    message=f"A CSV expense sheet '{session.file_name}' was successfully imported into group '{group.name}'."
                )
        
        messages.success(
            request,
            f"Import complete! {summary['imported']} expense(s) and "
            f"{summary['settlements_created']} settlement(s) imported. "
            f"{summary['skipped']} row(s) skipped."
        )
    except Exception as e:
        messages.error(request, f'Import failed: {e}')
        return redirect('splitly:csv_import_review', group_id=group.id, session_id=session.id)

    return redirect('splitly:csv_import_report', group_id=group.id, session_id=session.id)


@login_required
def csv_import_report(request, group_id, session_id):
    """Step 4: Show the post-import summary report."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    session = get_object_or_404(CSVImport, id=session_id, group=group)

    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden('Access denied.')

    report = build_report(session)
    return render(request, 'splitly/csv_import_report.html', {
        'group': group,
        'report': report,
        'current_page': 'groups',
    })


# ── Settlement ──────────────────────────────────────────────────────────────────

@login_required
@transaction.atomic
def settlement_create(request, group_id):
    """Record a manual settlement payment between two group members."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False, is_archived=False)
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden('Access denied.')

    cycle = group.current_cycle
    if not cycle:
        messages.error(request, 'No active expense cycle found.')
        return redirect('splitly:group_detail', group_id=group.id)

    members = list(cycle.members.all())

    if request.method == 'POST':
        form = SettlementForm(request.POST, members=members)
        if form.is_valid():
            s = form.save(commit=False)
            s.group = group
            s.cycle = cycle
            s.created_by = request.user
            # Currency conversion
            s.exchange_rate = get_rate(s.currency)
            s.converted_inr_value = convert_to_inr(s.amount, s.currency)
            s.save()
            
            # Log Audit & Notifications
            AuditLog.objects.create(
                user=request.user,
                action='settlement_added',
                description=f"Settlement: {s.payer.email} paid {s.receiver.email} {s.currency} {s.amount} in '{group.name}'."
            )
            Notification.objects.create(
                user=s.receiver,
                notification_type='settlement_completed',
                title='Settlement Received',
                message=f"{s.payer.get_full_name() or s.payer.email} paid you {s.currency} {s.amount} in group '{group.name}'."
            )
            
            messages.success(
                request,
                f"Settlement recorded: {s.payer.get_full_name() or s.payer.email} → "
                f"{s.receiver.get_full_name() or s.receiver.email} — "
                f"{format_currency(s.amount, s.currency)}"
            )
            return redirect('splitly:settlement_list', group_id=group.id)
    else:
        form = SettlementForm(members=members, initial={
            'date': timezone.now().date(),
            'payer': request.user,
            'currency': request.user.preferred_currency,
        })

    return render(request, 'splitly/settlement_form.html', {
        'form': form,
        'group': group,
        'cycle': cycle,
        'members': members,
        'current_page': 'groups',
    })


@login_required
def settlement_list(request, group_id):
    """List all settlements in a group, newest first."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden('Access denied.')

    settlements = Settlement.objects.filter(group=group).order_by('-date', '-created_at')
    cycle = group.current_cycle
    total_inr = sum(s.converted_inr_value or 0 for s in settlements)

    return render(request, 'splitly/settlement_list.html', {
        'group': group,
        'settlements': settlements,
        'cycle': cycle,
        'total_inr': total_inr,
        'current_page': 'groups',
    })


# ── Balance Detail ─────────────────────────────────────────────────────────────

@login_required
def balance_detail(request, group_id):
    """Full balance transparency view with per-member paid/shared/pending breakdown."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden('Access denied.')

    cycle = group.current_cycle
    if not cycle:
        messages.error(request, 'No active cycle found.')
        return redirect('splitly:group_detail', group_id=group.id)

    display_currency = request.GET.get('currency', request.user.preferred_currency)
    if display_currency not in SUPPORTED_CURRENCIES:
        display_currency = 'INR'

    balance_data = get_group_balances(group, cycle, display_currency=display_currency)

    return render(request, 'splitly/balance_detail.html', {
        'group': group,
        'cycle': cycle,
        'balance_data': balance_data,
        'display_currency': display_currency,
        'supported_currencies': SUPPORTED_CURRENCIES,
        'current_page': 'groups',
    })


# ── Exchange Rates ─────────────────────────────────────────────────────────────

@login_required
def exchange_rates(request):
    """View and update exchange rates (all logged-in users can view; update requires staff)."""
    seed_default_rates(user=None)
    rates = Currency.objects.all().order_by('currency')
    all_rates = get_all_rates()

    if request.method == 'POST' and request.user.is_staff:
        currency = request.POST.get('currency', '').upper()
        new_rate = request.POST.get('rate_to_inr', '')
        try:
            from decimal import Decimal as D
            rate_obj, _ = Currency.objects.get_or_create(
                currency=currency,
                defaults={'rate_to_inr': D(new_rate)}
            )
            rate_obj.rate_to_inr = D(new_rate)
            rate_obj.updated_by = request.user
            rate_obj.save()
            messages.success(request, f'Rate for {currency} updated to {new_rate}.')
        except Exception as e:
            messages.error(request, f'Could not update rate: {e}')
        return redirect('splitly:exchange_rates')

    return render(request, 'splitly/exchange_rates.html', {
        'rates': rates,
        'all_rates': all_rates,
        'is_staff': request.user.is_staff,
        'current_page': 'settings',
    })


# ─── PHASE 3 NEW VIEWS ─────────────────────────────────────────────────────────

def _generate_ai_insights(expenses, monthly_data, category_data, member_data, currency_data, groups):
    """Generate deterministic AI insights from real expense data."""
    insights = []
    today = date.today()
    this_month = today.strftime('%Y-%m')
    last_month = (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')

    # 1. Top category change
    if category_data:
        top_cat = max(category_data, key=category_data.get)
        top_val = category_data[top_cat]
        total = sum(category_data.values())
        pct = (top_val / total * 100) if total > 0 else 0
        insights.append({
            'icon': 'bi-pie-chart-fill',
            'color': '#f97316',
            'text': f"{top_cat} is your top spending category at {pct:.0f}% of total expenses."
        })

    # 2. Monthly comparison
    this_val = float(monthly_data.get(this_month, 0))
    last_val = float(monthly_data.get(last_month, 0))
    if last_val > 0 and this_val > 0:
        change = ((this_val - last_val) / last_val) * 100
        direction = 'increased' if change > 0 else 'decreased'
        insights.append({
            'icon': 'bi-graph-up-arrow' if change > 0 else 'bi-graph-down-arrow',
            'color': '#ef4444' if change > 0 else '#22c55e',
            'text': f"Spending {direction} by {abs(change):.0f}% compared to last month."
        })
    elif this_val > 0:
        insights.append({
            'icon': 'bi-calendar-check',
            'color': '#3b82f6',
            'text': f"You've spent ₹{this_val:,.0f} so far this month."
        })

    # 3. Top spender
    if member_data:
        top_member = max(member_data, key=member_data.get)
        insights.append({
            'icon': 'bi-person-fill-up',
            'color': '#a855f7',
            'text': f"{top_member} is the highest contributor with ₹{float(member_data[top_member]):,.0f} paid."
        })

    # 4. Foreign currency usage
    total_currency_count = sum(float(v) for v in currency_data.values())
    foreign_count = sum(float(v) for k, v in currency_data.items() if k != 'INR')
    if total_currency_count > 0 and foreign_count > 0:
        foreign_pct = (foreign_count / total_currency_count) * 100
        insights.append({
            'icon': 'bi-currency-exchange',
            'color': '#0d9488',
            'text': f"Foreign currency expenses account for {foreign_pct:.0f}% of all transactions."
        })

    # 5. Recent member activity
    recent_memberships = Membership.objects.filter(
        group__in=groups,
        join_date__gte=timezone.now() - timedelta(days=30),
        status='active'
    ).select_related('user', 'group')
    if recent_memberships.exists():
        m = recent_memberships.first()
        insights.append({
            'icon': 'bi-person-plus-fill',
            'color': '#22c55e',
            'text': f"{m.user.get_full_name() or m.user.email} joined '{m.group.name}' recently."
        })

    # 6. Highest spend month ever
    if monthly_data:
        peak_month_key = max(monthly_data, key=monthly_data.get)
        peak_val = float(monthly_data[peak_month_key])
        peak_label = datetime.strptime(peak_month_key, '%Y-%m').strftime('%B %Y')
        insights.append({
            'icon': 'bi-trophy-fill',
            'color': '#eab308',
            'text': f"Highest spending was in {peak_label} at ₹{peak_val:,.0f}."
        })

    # 7. Import summary
    csv_count = CSVImport.objects.filter(group__in=groups, status='completed').count()
    pdf_count = PDFImport.objects.filter(group__in=groups, status='completed').count()
    if csv_count > 0 or pdf_count > 0:
        parts = []
        if csv_count: parts.append(f"{csv_count} CSV")
        if pdf_count: parts.append(f"{pdf_count} PDF")
        insights.append({
            'icon': 'bi-cloud-upload-fill',
            'color': '#64748b',
            'text': f"You've completed {' and '.join(parts)} import(s) across your groups."
        })

    return insights


@login_required
def analytics_view(request):
    """Backend processor for user spend analytics, category, member share charts, heatmap, AI insights."""
    user = request.user
    memberships = Membership.objects.filter(user=user, status='active', group__is_deleted=False)
    groups = [m.group for m in memberships]

    # Non-deleted expenses involving or paid in the groups
    cycles = ExpenseCycle.objects.filter(group__in=groups)
    expenses = Expense.objects.filter(cycle__in=cycles, is_deleted=False)

    monthly_data = defaultdict(Decimal)
    category_data = defaultdict(Decimal)
    member_data = defaultdict(Decimal)
    currency_data = defaultdict(Decimal)
    daily_spend = defaultdict(Decimal)  # for heatmap

    for exp in expenses:
        inr_val = exp.converted_inr_value

        # Monthly aggregate
        month_str = exp.date.strftime('%Y-%m')
        monthly_data[month_str] += inr_val

        # Category aggregate
        category_data[exp.category] += inr_val

        # Currency count
        currency_data[exp.currency] += Decimal('1')

        # Total member contribution
        member_data[exp.paid_by.get_full_name() or exp.paid_by.email] += inr_val

        # Daily spend for heatmap (last 12 weeks)
        day_str = exp.date.strftime('%Y-%m-%d')
        daily_spend[day_str] += inr_val

    # Sort monthly spent
    sorted_months = sorted(monthly_data.keys())
    monthly_labels = [datetime.strptime(m, '%Y-%m').strftime('%b %Y') for m in sorted_months]
    monthly_values = [float(monthly_data[m]) for m in sorted_months]

    cat_labels = list(category_data.keys())
    cat_values = [float(category_data[k]) for k in cat_labels]

    curr_labels = list(currency_data.keys())
    curr_values = [float(currency_data[k]) for k in curr_labels]

    sorted_members = sorted(member_data.items(), key=lambda x: x[1], reverse=True)[:10]
    member_labels = [item[0] for item in sorted_members]
    member_values = [float(item[1]) for item in sorted_members]

    # ── Heatmap data: last 84 days (12 weeks) ──
    today = date.today()
    heatmap_data = []
    for i in range(83, -1, -1):
        d = today - timedelta(days=i)
        day_key = d.strftime('%Y-%m-%d')
        heatmap_data.append({
            'date': day_key,
            'weekday': d.strftime('%a'),
            'value': float(daily_spend.get(day_key, 0)),
        })

    # ── AI Insights ──
    ai_insights = _generate_ai_insights(
        expenses, monthly_data, category_data, member_data, currency_data, groups
    )

    # ── Import summaries ──
    csv_imports = CSVImport.objects.filter(group__in=groups).order_by('-uploaded_at')[:5]
    pdf_imports = PDFImport.objects.filter(group__in=groups).order_by('-uploaded_at')[:5]

    context = {
        'monthly_labels': json.dumps(monthly_labels),
        'monthly_values': json.dumps(monthly_values),
        'cat_labels': json.dumps(cat_labels),
        'cat_values': json.dumps(cat_values),
        'curr_labels': json.dumps(curr_labels),
        'curr_values': json.dumps(curr_values),
        'member_labels': json.dumps(member_labels),
        'member_values': json.dumps(member_values),
        'trend_labels': json.dumps(monthly_labels),
        'trend_values': json.dumps(monthly_values),
        'heatmap_data': json.dumps(heatmap_data),
        'ai_insights': ai_insights,
        'csv_imports': csv_imports,
        'pdf_imports': pdf_imports,
        'current_page': 'analytics',
    }
    return render(request, 'splitly/analytics.html', context)


@login_required
def notifications_view(request):
    """View and clear user-specific notifications."""
    notifications = request.user.notifications.all().order_by('-created_at')
    context = {
        'notifications': notifications,
        'current_page': 'notifications'
    }
    return render(request, 'splitly/notifications_list.html', context)


@login_required
@require_POST
def mark_notification_read(request, notif_id):
    notif = get_object_or_404(Notification, id=notif_id, user=request.user)
    notif.is_read = True
    notif.save()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def mark_all_notifications_read(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    messages.success(request, "All notifications marked as read.")
    return redirect('splitly:notifications_view')


@login_required
@require_POST
def clear_all_notifications(request):
    request.user.notifications.all().delete()
    messages.success(request, "Notifications history cleared.")
    return redirect('splitly:notifications_view')


# ── PDF Import Pipeline ────────────────────────────────────────────────────────

@login_required
def pdf_import_upload(request, group_id):
    """Step 1: Upload a PDF file for AI-powered expense extraction."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False, is_archived=False)
    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden('Access denied.')

    if request.method == 'POST':
        form = PDFImportUploadForm(request.POST, request.FILES)
        if form.is_valid():
            pdf_file = form.cleaned_data['pdf_file']
            try:
                extracted_text = parse_pdf(pdf_file)
                rows = extract_expenses_from_text(extracted_text)
            except ValueError as e:
                messages.error(request, str(e))
                return render(request, 'splitly/pdf_import_upload.html', {'form': form, 'group': group})

            if not rows:
                messages.warning(request, "No expense entries could be extracted from this PDF. "
                                          "The document may not contain recognisable financial data.")
                return render(request, 'splitly/pdf_import_upload.html', {'form': form, 'group': group})

            # Save session
            session = PDFImport.objects.create(
                group=group,
                uploaded_by=request.user,
                file_name=pdf_file.name,
                status='pending',
                extracted_text=extracted_text[:50000],  # truncate safety
            )
            # Save PDF file
            session.file.save(pdf_file.name, ContentFile(pdf_file.read()))
            session.save()

            # Validate + annotate rows
            validate_pdf_rows(session, rows, group)

            messages.info(
                request,
                f"PDF uploaded: {session.total_rows} expense(s) extracted by AI. "
                f"{session.valid_rows} valid, {session.invalid_rows} need review."
            )
            return redirect('splitly:pdf_import_review', group_id=group.id, session_id=session.id)
    else:
        form = PDFImportUploadForm()

    past_imports = PDFImport.objects.filter(group=group, uploaded_by=request.user).order_by('-uploaded_at')[:10]
    return render(request, 'splitly/pdf_import_upload.html', {
        'form': form,
        'group': group,
        'past_imports': past_imports,
        'current_page': 'groups',
    })


@login_required
def pdf_import_review(request, group_id, session_id):
    """Step 2: Display extracted rows for user verification before importing."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    session = get_object_or_404(PDFImport, id=session_id, group=group)

    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden('Access denied.')

    if session.status == 'completed':
        return redirect('splitly:pdf_import_report', group_id=group.id, session_id=session.id)

    rows = session.pdf_rows.all()
    anomaly_rows = rows.filter(status='anomaly')
    valid_rows = rows.filter(status='valid')
    pending_decisions = anomaly_rows.filter(user_decision='pending').count()

    return render(request, 'splitly/pdf_import_review.html', {
        'group': group,
        'session': session,
        'valid_rows': valid_rows,
        'anomaly_rows': anomaly_rows,
        'pending_decisions': pending_decisions,
        'current_page': 'groups',
    })


@login_required
@require_POST
def pdf_import_decide(request, group_id, session_id):
    """AJAX/POST endpoint: record approve/reject for a single extracted row."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    session = get_object_or_404(PDFImport, id=session_id, group=group)

    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return JsonResponse({'error': 'Access denied'}, status=403)

    row_id = request.POST.get('row_id')
    decision = request.POST.get('decision')
    note = request.POST.get('note', '')

    if decision not in ('approve', 'reject'):
        return JsonResponse({'error': 'Invalid decision'}, status=400)

    try:
        row = ImportLog.objects.get(id=row_id, pdf_import_session=session)
        row.user_decision = decision
        row.decision_note = note
        row.save()
        pending = session.pdf_rows.filter(status='anomaly', user_decision='pending').count()
        return JsonResponse({'ok': True, 'pending': pending})
    except ImportLog.DoesNotExist:
        return JsonResponse({'error': 'Row not found'}, status=404)


@login_required
@require_POST
def pdf_import_execute(request, group_id, session_id):
    """Step 3: Execute import — commit all approved extracted rows."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    session = get_object_or_404(PDFImport, id=session_id, group=group)

    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden('Access denied.')

    if session.status == 'completed':
        messages.warning(request, 'This import has already been executed.')
        return redirect('splitly:pdf_import_report', group_id=group.id, session_id=session.id)

    # Check all anomalies are decided
    pending = session.pdf_rows.filter(status='anomaly', user_decision='pending').count()
    if pending > 0:
        messages.error(request, f'{pending} flagged row(s) still need your decision before importing.')
        return redirect('splitly:pdf_import_review', group_id=group.id, session_id=session.id)

    try:
        summary = execute_pdf_import(session, request.user)

        AuditLog.objects.create(
            user=request.user,
            action='pdf_imported',
            description=f"PDF '{session.file_name}' imported to group '{group.name}': "
                        f"{summary['imported']} items imported."
        )
        active_members = group.get_active_members()
        for member in active_members:
            if member != request.user:
                Notification.objects.create(
                    user=member,
                    notification_type='pdf_imported',
                    title='PDF Import Completed',
                    message=f"A PDF expense file '{session.file_name}' was imported into "
                            f"group '{group.name}' by {request.user.email}."
                )

        messages.success(
            request,
            f"PDF import complete! {summary['imported']} expense(s) imported, "
            f"{summary['skipped']} row(s) skipped."
        )
    except Exception as e:
        messages.error(request, f'Import failed: {e}')
        return redirect('splitly:pdf_import_review', group_id=group.id, session_id=session.id)

    return redirect('splitly:pdf_import_report', group_id=group.id, session_id=session.id)


@login_required
def pdf_import_report(request, group_id, session_id):
    """Step 4: Post-import summary report."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    session = get_object_or_404(PDFImport, id=session_id, group=group)

    if not Membership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden('Access denied.')

    report = build_pdf_report(session)
    return render(request, 'splitly/pdf_import_report.html', {
        'group': group,
        'report': report,
        'current_page': 'groups',
    })


@login_required
@staff_member_required
def admin_dashboard_view(request):
    """Custom staff-only admin dashboard."""
    users = User.objects.all().order_by('id')
    groups = Group.objects.all().order_by('id')
    expenses = Expense.objects.all().order_by('-date', '-created_at')
    settlements = Settlement.objects.all().order_by('-date', '-created_at')
    csv_imports = CSVImport.objects.all().order_by('-uploaded_at')
    reports = PDFReport.objects.all().order_by('-generated_at')
    audit_logs = AuditLog.objects.all().order_by('-timestamp')
    
    q = request.GET.get('q', '').strip()
    if q:
        audit_logs = audit_logs.filter(
            models.Q(description__icontains=q) | 
            models.Q(action__icontains=q) |
            models.Q(user__email__icontains=q)
        )
        
    context = {
        'users': users,
        'groups': groups,
        'expenses': expenses,
        'settlements': settlements,
        'csv_imports': csv_imports,
        'reports': reports,
        'audit_logs': audit_logs,
        'q': q,
        'current_page': 'admin',
    }
    return render(request, 'splitly/admin_dashboard.html', context)


@login_required
@staff_member_required
@require_POST
def admin_toggle_user_status(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    if target_user == request.user:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect('splitly:admin_dashboard')
    target_user.is_active = not target_user.is_active
    target_user.save()
    messages.success(request, f"User '{target_user.email}' active status toggled.")
    return redirect('splitly:admin_dashboard')


@login_required
@staff_member_required
@require_POST
def admin_toggle_group_archive(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    group.is_archived = not group.is_archived
    group.save()
    messages.success(request, f"Group '{group.name}' archived status toggled.")
    return redirect('splitly:admin_dashboard')


@login_required
@staff_member_required
@require_POST
def admin_toggle_group_delete(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    group.is_deleted = not group.is_deleted
    group.save()
    messages.success(request, f"Group '{group.name}' deletion status toggled.")
    return redirect('splitly:admin_dashboard')


@login_required
@staff_member_required
@require_POST
def admin_toggle_expense_delete(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id)
    expense.is_deleted = not expense.is_deleted
    expense.save()
    messages.success(request, f"Expense '{expense.title}' deletion status toggled.")
    return redirect('splitly:admin_dashboard')


@login_required
@staff_member_required
@require_POST
def admin_delete_settlement(request, settlement_id):
    settlement = get_object_or_404(Settlement, id=settlement_id)
    # Revert related Settlement expense if any
    try:
        title = f"Settlement: {settlement.payer.first_name or settlement.payer.email} paid {settlement.receiver.first_name or settlement.receiver.email}"
        exp = Expense.objects.filter(cycle=settlement.cycle, title=title, amount=settlement.amount, is_deleted=False).first()
        if exp:
            exp.is_deleted = True
            exp.save()
    except Exception:
        pass
    
    settlement.delete()
    messages.success(request, "Settlement record deleted and reverted successfully.")
    return redirect('splitly:admin_dashboard')
