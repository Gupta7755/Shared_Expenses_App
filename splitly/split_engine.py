from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
import pandas as pd

def round_decimal(value):
    if isinstance(value, float):
        value = Decimal(str(value))
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def calculate_splits(amount, split_type, participants, inputs=None):
    """
    Calculates the exact split amount for each participant.
    
    Parameters:
    - amount (Decimal): Total expense amount
    - split_type (str): 'equal', 'unequal', 'percentage', 'share'
    - participants (list of Users): Users participating in the expense
    - inputs (dict): User ID -> input value (amount, percentage, or share count)
    
    Returns:
    - dict: User -> Decimal (split amount)
    - str: Transparent calculation breakdown text
    """
    amount = Decimal(str(amount))
    n = len(participants)
    if n == 0:
        raise ValidationError("An expense must have at least one participant.")
        
    splits = {}
    breakdown = []

    if split_type == 'equal':
        # Calculate base share
        base_share = round_decimal(amount / n)
        # Sum of shares might be slightly off due to rounding
        total_calculated = base_share * n
        diff = amount - total_calculated
        
        # Populate splits
        for p in participants:
            splits[p] = base_share
            
        # Distribute the penny difference to the first participant
        if diff != 0 and n > 0:
            splits[participants[0]] += diff
            
        breakdown.append(f"Total Amount: {amount}")
        breakdown.append(f"Split equally among {n} participants.")
        breakdown.append(f"Base share: {base_share} each.")
        if diff != 0:
            breakdown.append(f"Rounding adjustment of {diff} applied to {participants[0]}.")

    elif split_type == 'unequal':
        if not inputs:
            raise ValidationError("Unequal split requires individual amounts.")
        
        total_input = Decimal('0.00')
        for p in participants:
            val = Decimal(str(inputs.get(p.id, 0)))
            splits[p] = round_decimal(val)
            total_input += splits[p]
            
        if total_input != amount:
            raise ValidationError(f"Sum of split amounts ({total_input}) must equal total expense amount ({amount}).")
            
        breakdown.append(f"Total Amount: {amount}")
        breakdown.append("Split unequally with custom values:")
        for p in participants:
            breakdown.append(f"- {p}: {splits[p]}")

    elif split_type == 'percentage':
        if not inputs:
            raise ValidationError("Percentage split requires percentages for each participant.")
            
        total_pct = Decimal('0.00')
        for p in participants:
            pct = Decimal(str(inputs.get(p.id, 0)))
            total_pct += pct
            # Calculate provisional split amount
            splits[p] = round_decimal(amount * (pct / Decimal('100.00')))
            
        if total_pct != Decimal('100.00'):
            raise ValidationError(f"Sum of percentages ({total_pct}%) must equal 100%.")
            
        # Adjust rounding errors to match total amount exactly
        total_calculated = sum(splits.values())
        diff = amount - total_calculated
        if diff != 0:
            splits[participants[0]] += diff
            
        breakdown.append(f"Total Amount: {amount}")
        breakdown.append("Split by percentage:")
        for p in participants:
            pct = Decimal(str(inputs.get(p.id, 0)))
            breakdown.append(f"- {p}: {pct}% of total = {splits[p]}")
        if diff != 0:
            breakdown.append(f"Rounding adjustment of {diff} applied to {participants[0]}.")

    elif split_type == 'share':
        if not inputs:
            raise ValidationError("Share split requires number of shares for each participant.")
            
        total_shares = Decimal('0.00')
        shares_map = {}
        for p in participants:
            sh = Decimal(str(inputs.get(p.id, 0)))
            shares_map[p] = sh
            total_shares += sh
            
        if total_shares <= 0:
            raise ValidationError("Total shares must be greater than 0.")
            
        # Calculate split per share
        for p in participants:
            sh = shares_map[p]
            splits[p] = round_decimal(amount * (sh / total_shares))
            
        # Adjust rounding errors
        total_calculated = sum(splits.values())
        diff = amount - total_calculated
        if diff != 0:
            splits[participants[0]] += diff
            
        breakdown.append(f"Total Amount: {amount}")
        breakdown.append(f"Split by shares (Total shares: {total_shares}):")
        for p in participants:
            sh = shares_map[p]
            breakdown.append(f"- {p}: {sh} share(s) = {splits[p]}")
        if diff != 0:
            breakdown.append(f"Rounding adjustment of {diff} applied to {participants[0]}.")

    else:
        raise ValidationError(f"Unknown split type: {split_type}")

    return splits, "\n".join(breakdown)


def calculate_cycle_balances(cycle):
    """
    Calculates the net balance for each member in the given ExpenseCycle.
    
    Net Balance = (Total Paid by User in Cycle) - (Total Owed by User in Cycle)
    
    Returns a pandas DataFrame and a dictionary of balances.
    """
    members = list(cycle.members.all())
    balances = {m: Decimal('0.00') for m in members}
    
    # Pre-populate with payments
    expenses = cycle.expenses.filter(is_deleted=False)
    for exp in expenses:
        # Payer gets credit for paying
        if exp.paid_by in balances:
            balances[exp.paid_by] += exp.amount
            
        # Participants owe their shares
        for split in exp.splits.all():
            if split.user in balances:
                balances[split.user] -= split.amount
                
    # Return as dict and pandas series/dataframe
    data = []
    for m, bal in balances.items():
        data.append({
            'user_id': m.id,
            'email': m.email,
            'name': m.get_full_name() or m.username,
            'balance': float(bal)
        })
        
    df = pd.DataFrame(data) if data else pd.DataFrame(columns=['user_id', 'email', 'name', 'balance'])
    return df, balances


def simplify_debts(balances):
    """
    Simplifies the transactions needed to settle all balances.
    
    Parameters:
    - balances (dict): User -> Decimal (positive: is owed, negative: owes)
    
    Returns:
    - list of dicts: [{'from_user': User, 'to_user': User, 'amount': Decimal}]
    """
    # Filter out zero balances
    creditors = [] # (User, balance)
    debtors = [] # (User, balance)
    
    for user, bal in balances.items():
        bal_val = round_decimal(bal)
        if bal_val > Decimal('0.01'):
            creditors.append([user, bal_val])
        elif bal_val < Decimal('-0.01'):
            debtors.append([user, abs(bal_val)])
            
    # Sort creditors descending (biggest first)
    creditors.sort(key=lambda x: x[1], reverse=True)
    # Sort debtors descending (biggest first)
    debtors.sort(key=lambda x: x[1], reverse=True)
    
    transactions = []
    
    while creditors and debtors:
        creditor_user, credit_amt = creditors[0]
        debtor_user, debt_amt = debtors[0]
        
        # Settle the minimum of what debtor owes vs what creditor is owed
        settle_amt = min(credit_amt, debt_amt)
        settle_amt = round_decimal(settle_amt)
        
        if settle_amt > 0:
            transactions.append({
                'from_user': debtor_user,
                'to_user': creditor_user,
                'amount': settle_amt
            })
            
        # Update balances
        creditors[0][1] -= settle_amt
        debtors[0][1] -= settle_amt
        
        # Remove if settled
        if creditors[0][1] < Decimal('0.01'):
            creditors.pop(0)
        else:
            creditors.sort(key=lambda x: x[1], reverse=True)
            
        if debtors[0][1] < Decimal('0.01'):
            debtors.pop(0)
        else:
            debtors.sort(key=lambda x: x[1], reverse=True)
            
    return transactions
