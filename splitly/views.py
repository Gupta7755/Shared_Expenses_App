from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.http import require_POST

from decimal import Decimal
from splitly.models import (
    User, Group, GroupMembership, ExpenseCycle, ExpenseCycleMember,
    Expense, ExpenseSplit, Settlement, ExchangeRate, CSVImport, CSVImportRow
)
from splitly.forms import (
    CustomUserCreationForm, UserProfileForm, GroupForm, ExpenseForm,
    CSVImportUploadForm, SettlementForm, ExchangeRateForm
)
from splitly.split_engine import calculate_splits, calculate_cycle_balances, simplify_debts
from splitly.balance_engine import get_group_balances, get_simple_balances, simplify_debts as simplify
from splitly.currency import convert_to_inr, get_rate, get_all_rates, seed_default_rates, format_currency, SUPPORTED_CURRENCIES
from splitly.csv_engine import parse_csv, validate_rows, execute_import, build_report
from splitly.reports import generate_expenses_csv, generate_group_pdf_report

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
            messages.success(request, f"Welcome back, {user.first_name or user.email}!")
            return redirect('splitly:dashboard')
        else:
            messages.error(request, "Invalid email or password.")
    return render(request, 'splitly/login.html')


@login_required
def logout_view(request):
    logout(request)
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
    active_memberships = GroupMembership.objects.filter(user=user, status='active', group__is_deleted=False)
    groups = [m.group for m in active_memberships]
    
    # Calculate global You Owe / You Are Owed / Net Balance
    total_owed = Decimal('0.00') # User owes others (negative balances)
    total_is_owed = Decimal('0.00') # Others owe user (positive balances)
    
    group_balances = []
    
    for g in groups:
        cycle = g.current_cycle
        if not cycle:
            continue
        _, cycle_balances = calculate_cycle_balances(cycle)
        user_bal = cycle_balances.get(user, Decimal('0.00'))
        
        group_balances.append({
            'group': g,
            'balance': user_bal,
            'members_count': cycle.members.count()
        })
        
        if user_bal > 0:
            total_is_owed += user_bal
        elif user_bal < 0:
            total_owed += abs(user_bal)
            
    net_balance = total_is_owed - total_owed
    
    # Get recent activity (recent expenses in the user's groups)
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
        'current_page': 'dashboard'
    }
    return render(request, 'splitly/dashboard.html', context)


@login_required
def group_list(request):
    memberships = GroupMembership.objects.filter(user=request.user, status='active', group__is_deleted=False)
    archived_memberships = GroupMembership.objects.filter(user=request.user, status='active', group__is_archived=True, group__is_deleted=False)
    
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
            GroupMembership.objects.create(
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
            
            messages.success(request, f"Group '{group.name}' created successfully!")
            return redirect('splitly:group_detail', group_id=group.id)
    else:
        form = GroupForm()
    return render(request, 'splitly/group_form.html', {'form': form, 'title': 'Create Group'})


@login_required
def group_detail(request, group_id):
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    
    # Check if user is member
    membership = GroupMembership.objects.filter(group=group, user=request.user, status='active').first()
    if not membership:
        return HttpResponseForbidden("You are not a member of this group.")
        
    # Get active cycle
    cycle = group.current_cycle
    if not cycle:
        # Auto-create if somehow missing
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
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    if request.method == 'POST':
        email = request.POST.get('email').strip()
        new_user = User.objects.filter(email=email).first()
        if not new_user:
            messages.error(request, f"No user found with email '{email}'.")
            return redirect('splitly:group_detail', group_id=group.id)
            
        # Check if already active member
        exists = GroupMembership.objects.filter(group=group, user=new_user, status='active').exists()
        if exists:
            messages.warning(request, f"'{new_user}' is already a member of this group.")
            return redirect('splitly:group_detail', group_id=group.id)
            
        # Change membership: old cycle is closed, new cycle started.
        current_cycle = group.current_cycle
        if current_cycle:
            current_cycle.status = 'closed'
            current_cycle.end_date = timezone.now()
            current_cycle.save()
            
        # Add membership history record (never delete historical ones)
        membership = GroupMembership.objects.filter(group=group, user=new_user).first()
        if membership:
            membership.status = 'active'
            membership.join_date = timezone.now()
            membership.leave_date = None
            membership.save()
        else:
            GroupMembership.objects.create(
                group=group,
                user=new_user,
                status='active'
            )
            
        # Create new cycle with all active members
        new_cycle = ExpenseCycle.objects.create(group=group, status='active')
        active_members = group.get_active_members()
        for member in active_members:
            ExpenseCycleMember.objects.create(cycle=new_cycle, user=member)
            
        messages.success(request, f"Added {new_user.get_full_name() or new_user.email} to the group. A new expense cycle has started.")
        
    return redirect('splitly:group_detail', group_id=group.id)


@login_required
@transaction.atomic
def group_remove_member(request, group_id, user_id):
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    # Check if current user is member
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    member_to_remove = get_object_or_404(User, id=user_id)
    
    # Check if target user is in the group
    membership = GroupMembership.objects.filter(group=group, user=member_to_remove, status='active').first()
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
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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
                expense.save()
                
                # Save split records
                for member, split_amount in splits.items():
                    ExpenseSplit.objects.create(
                        expense=expense,
                        user=member,
                        amount=split_amount,
                        input_value=Decimal(str(inputs.get(member.id, 0)))
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
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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
                updated_expense.save()
                
                # Remove old splits and create new ones
                expense.splits.all().delete()
                for member, split_amount in splits.items():
                    ExpenseSplit.objects.create(
                        expense=updated_expense,
                        user=member,
                        amount=split_amount,
                        input_value=Decimal(str(inputs.get(member.id, 0)))
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
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    expense.is_deleted = True
    expense.save()
    messages.success(request, f"Expense '{expense.title}' has been deleted.")
    return redirect('splitly:group_detail', group_id=group.id)


@login_required
def expense_detail(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id, is_deleted=False)
    group = expense.cycle.group
    
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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
    memberships = GroupMembership.objects.filter(user=request.user, status='active', group__is_deleted=False)
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
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
        return HttpResponseForbidden("Access denied.")
        
    # Export all expenses of all cycles for this group
    cycles = group.cycles.all()
    expenses = Expense.objects.filter(cycle__in=cycles, is_deleted=False)
    return generate_expenses_csv(expenses)


@login_required
def download_group_pdf(request, group_id, cycle_id=None):
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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
    return generate_group_pdf_report(group, cycle, cycle_balances, transactions)


@login_required
def balances_view(request):
    """
    Displays settlements and balances overview across all user groups.
    """
    memberships = GroupMembership.objects.filter(user=request.user, status='active', group__is_deleted=False)
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
    """
    Records a settlement payment of a specific amount from the current user 
    to another user within the group cycle.
    This creates an expense with Paid By = Current User, split unequally 
    where the target user owes 100% of the amount.
    """
    group = get_object_or_404(Group, id=group_id, is_deleted=False, is_archived=False)
    cycle = group.current_cycle
    if not cycle:
        return redirect('splitly:group_detail', group_id=group.id)
        
    # Check if both are members of the cycle
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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
            date=timezone.now().date(),
            category='Other',
            paid_by=request.user,
            split_type='unequal',
            description=f"Direct settlement payment between {request.user} and {to_user}.",
            created_by=request.user
        )
        
        # Target user owes the whole amount
        ExpenseSplit.objects.create(
            expense=expense,
            user=to_user,
            amount=amount,
            input_value=amount
        )
        
        # Payer owes 0.00
        ExpenseSplit.objects.create(
            expense=expense,
            user=request.user,
            amount=Decimal('0.00'),
            input_value=Decimal('0.00')
        )
        
        # Zero out other members in cycle splits (they don't participate)
        for member in cycle.members.all():
            if member != request.user and member != to_user:
                ExpenseSplit.objects.create(
                    expense=expense,
                    user=member,
                    amount=Decimal('0.00'),
                    input_value=Decimal('0.00')
                )
                
        messages.success(request, f"Settlement of {amount} to {to_user.get_full_name() or to_user.email} recorded!")
        
    return redirect('splitly:group_detail', group_id=group.id)


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 VIEWS
# ─────────────────────────────────────────────────────────────────────────────

# ── CSV Import ─────────────────────────────────────────────────────────────────

@login_required
def csv_import_upload(request, group_id):
    """Step 1: Upload a CSV file for a group."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False, is_archived=False)
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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

    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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

    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
        return JsonResponse({'error': 'Access denied'}, status=403)

    row_id = request.POST.get('row_id')
    decision = request.POST.get('decision')  # 'approve' or 'reject'
    note = request.POST.get('note', '')

    if decision not in ('approve', 'reject'):
        return JsonResponse({'error': 'Invalid decision'}, status=400)

    try:
        row = CSVImportRow.objects.get(id=row_id, import_session=session)
        row.user_decision = decision
        row.decision_note = note
        row.save()
        pending = session.rows.filter(status='anomaly', user_decision='pending').count()
        return JsonResponse({'ok': True, 'pending': pending})
    except CSVImportRow.DoesNotExist:
        return JsonResponse({'error': 'Row not found'}, status=404)


@login_required
@require_POST
def csv_import_execute(request, group_id, session_id):
    """Step 3: Execute the import — processes all approved rows."""
    group = get_object_or_404(Group, id=group_id, is_deleted=False)
    session = get_object_or_404(CSVImport, id=session_id, group=group)

    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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

    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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
    if not GroupMembership.objects.filter(group=group, user=request.user, status='active').exists():
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
    # Ensure defaults are seeded
    seed_default_rates(user=None)
    rates = ExchangeRate.objects.all().order_by('currency')
    all_rates = get_all_rates()

    if request.method == 'POST' and request.user.is_staff:
        currency = request.POST.get('currency', '').upper()
        new_rate = request.POST.get('rate_to_inr', '')
        try:
            from decimal import Decimal as D
            rate_obj, _ = ExchangeRate.objects.get_or_create(
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
