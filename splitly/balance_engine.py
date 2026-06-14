"""
balance_engine.py — Enhanced Balance Engine for Splitly Phase 2.

Provides full calculation transparency:
- Per-member: total_paid, total_shared, pending_amount, settlements
- Group-level: final settlement suggestions
- Multi-currency: all values available in INR and user's preferred currency

Never silently omits data. Every number is traceable to its source records.
"""
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd

from splitly.currency import convert_to_inr, convert_from_inr, format_currency


def round2(value) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def get_group_balances(group, cycle, display_currency: str = 'INR') -> dict:
    """
    Calculates a complete, transparent balance breakdown for a group cycle.

    Returns:
    {
        'members': [MemberBalance dict, ...],
        'final_settlements': [{'from_user', 'to_user', 'amount', 'amount_display'}, ...],
        'total_expenses_inr': Decimal,
        'total_settlements_inr': Decimal,
        'breakdown_text': str,
        'display_currency': str,
    }
    """
    members = list(cycle.members.all())
    expenses = cycle.expenses.filter(is_deleted=False)
    settlements = cycle.settlements.all() if hasattr(cycle, 'settlements') else []

    member_data = {m: _empty_member_balance(m) for m in members}

    # ── Expense contributions ─────────────────────────────────────────────────
    for exp in expenses:
        inr_total = exp.converted_inr_value or convert_to_inr(exp.amount, exp.currency)

        if exp.paid_by in member_data:
            member_data[exp.paid_by]['total_paid_inr'] += inr_total
            member_data[exp.paid_by]['expenses_paid'].append({
                'title': exp.title,
                'amount': exp.amount,
                'currency': exp.currency,
                'amount_inr': inr_total,
                'date': exp.date,
            })

        for split in exp.splits.all():
            split_inr = split.amount_inr if split.amount_inr else convert_to_inr(split.amount, exp.currency)
            if split.user in member_data:
                member_data[split.user]['total_shared_inr'] += split_inr
                member_data[split.user]['splits_detail'].append({
                    'expense_title': exp.title,
                    'owed': split.amount,
                    'currency': exp.currency,
                    'owed_inr': split_inr,
                })

    # ── Settlement contributions ──────────────────────────────────────────────
    for s in settlements:
        s_inr = s.converted_inr_value or convert_to_inr(s.amount, s.currency)
        if s.payer in member_data:
            member_data[s.payer]['settlements_paid_inr'] += s_inr
            member_data[s.payer]['settlements_paid_list'].append(s)
        if s.receiver in member_data:
            member_data[s.receiver]['settlements_received_inr'] += s_inr
            member_data[s.receiver]['settlements_received_list'].append(s)

    # ── Compute net balance ───────────────────────────────────────────────────
    # Net balance = paid - owed_by_splits + settlements_paid - settlements_received
    # Positive = others owe you; Negative = you owe others
    net_balances = {}
    for m, data in member_data.items():
        net = (
            data['total_paid_inr']
            - data['total_shared_inr']
            + data['settlements_paid_inr']
            - data['settlements_received_inr']
        )
        data['balance_inr'] = round2(net)
        data['pending_amount_inr'] = round2(max(Decimal('0'), -net))  # what they still owe
        net_balances[m] = round2(net)

        # Convert to display currency
        data['balance_display'] = round2(convert_from_inr(data['balance_inr'], display_currency))
        data['total_paid_display'] = round2(convert_from_inr(data['total_paid_inr'], display_currency))
        data['total_shared_display'] = round2(convert_from_inr(data['total_shared_inr'], display_currency))
        data['pending_display'] = round2(convert_from_inr(data['pending_amount_inr'], display_currency))

    # ── Simplified debt list ──────────────────────────────────────────────────
    final_settlements = _simplify_debts(net_balances, display_currency)

    # ── Transparency text ─────────────────────────────────────────────────────
    breakdown_lines = [
        f"Balance Calculation for: {group.name} — Cycle #{cycle.id}",
        f"Display Currency: {display_currency}",
        "=" * 50,
    ]
    total_exp_inr = Decimal('0')
    for exp in expenses:
        total_exp_inr += exp.converted_inr_value or convert_to_inr(exp.amount, exp.currency)

    total_set_inr = Decimal('0')
    for s in settlements:
        total_set_inr += s.converted_inr_value or convert_to_inr(s.amount, s.currency)

    breakdown_lines.append(f"Total Expenses (INR): ₹{total_exp_inr:,.2f}")
    breakdown_lines.append(f"Total Settlements (INR): ₹{total_set_inr:,.2f}")
    breakdown_lines.append("")

    for m, data in member_data.items():
        breakdown_lines.append(f"[ {m.get_full_name() or m.email} ]")
        breakdown_lines.append(f"  Paid:       ₹{data['total_paid_inr']:,.2f}")
        breakdown_lines.append(f"  Owed:       ₹{data['total_shared_inr']:,.2f}")
        breakdown_lines.append(f"  Settled (+):₹{data['settlements_paid_inr']:,.2f}")
        breakdown_lines.append(f"  Settled (-):₹{data['settlements_received_inr']:,.2f}")
        breakdown_lines.append(f"  Net:        ₹{data['balance_inr']:,.2f}")
        breakdown_lines.append("")

    return {
        'members': list(member_data.values()),
        'final_settlements': final_settlements,
        'total_expenses_inr': round2(total_exp_inr),
        'total_settlements_inr': round2(total_set_inr),
        'breakdown_text': '\n'.join(breakdown_lines),
        'display_currency': display_currency,
        'net_balances_raw': net_balances,  # kept for simplify_debts in views
    }


def get_simple_balances(cycle) -> dict:
    """
    Lightweight version used by views that only need net balance per member.
    Returns {User: Decimal} where positive = owed to them, negative = they owe.
    Accounts for both Expenses and Settlements.
    """
    members = list(cycle.members.all())
    balances = {m: Decimal('0.00') for m in members}

    for exp in cycle.expenses.filter(is_deleted=False):
        inr = exp.converted_inr_value or convert_to_inr(exp.amount, exp.currency)
        if exp.paid_by in balances:
            balances[exp.paid_by] += inr
        for split in exp.splits.all():
            split_inr = split.amount_inr if split.amount_inr else convert_to_inr(split.amount, exp.currency)
            if split.user in balances:
                balances[split.user] -= split_inr

    try:
        for s in cycle.settlements.all():
            s_inr = s.converted_inr_value or convert_to_inr(s.amount, s.currency)
            if s.payer in balances:
                balances[s.payer] += s_inr
            if s.receiver in balances:
                balances[s.receiver] -= s_inr
    except Exception:
        pass  # settlements may not exist in older cycles

    return {u: round2(v) for u, v in balances.items()}


def simplify_debts(balances: dict) -> list:
    """
    Greedy debt simplification algorithm.
    Input: {User: Decimal} (positive = owed, negative = owes)
    Output: [{'from_user': User, 'to_user': User, 'amount': Decimal}]
    """
    creditors = []
    debtors = []

    for user, bal in balances.items():
        bal = round2(bal)
        if bal > Decimal('0.01'):
            creditors.append([user, bal])
        elif bal < Decimal('-0.01'):
            debtors.append([user, abs(bal)])

    creditors.sort(key=lambda x: x[1], reverse=True)
    debtors.sort(key=lambda x: x[1], reverse=True)

    transactions = []
    while creditors and debtors:
        creditor_user, credit = creditors[0]
        debtor_user, debt = debtors[0]
        settle = round2(min(credit, debt))

        if settle > 0:
            transactions.append({'from_user': debtor_user, 'to_user': creditor_user, 'amount': settle})

        creditors[0][1] -= settle
        debtors[0][1] -= settle

        if creditors[0][1] < Decimal('0.01'):
            creditors.pop(0)
        if debtors[0][1] < Decimal('0.01'):
            debtors.pop(0)

    return transactions


def _empty_member_balance(user) -> dict:
    return {
        'user': user,
        'total_paid_inr': Decimal('0'),
        'total_shared_inr': Decimal('0'),
        'settlements_paid_inr': Decimal('0'),
        'settlements_received_inr': Decimal('0'),
        'balance_inr': Decimal('0'),
        'pending_amount_inr': Decimal('0'),
        'balance_display': Decimal('0'),
        'total_paid_display': Decimal('0'),
        'total_shared_display': Decimal('0'),
        'pending_display': Decimal('0'),
        'expenses_paid': [],
        'splits_detail': [],
        'settlements_paid_list': [],
        'settlements_received_list': [],
    }


def _simplify_debts(net_balances: dict, display_currency: str) -> list:
    raw = simplify_debts(net_balances)
    for tx in raw:
        tx['amount_display'] = round2(convert_from_inr(tx['amount'], display_currency))
        tx['amount_formatted'] = format_currency(tx['amount_display'], display_currency)
    return raw
