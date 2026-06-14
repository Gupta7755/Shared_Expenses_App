"""
csv_engine.py — CSV Import Engine for Splitly Phase 2.

Pipeline:
  1. parse_csv()         → list of raw row dicts
  2. validate_rows()     → creates CSVImportRow records with anomaly flags
  3. execute_import()    → imports only approved/valid rows into DB
  4. build_report()      → returns summary stats dict

Never silently changes data. All anomalies require user decision.
"""
import io
import re
from decimal import Decimal, InvalidOperation
from datetime import date, datetime

import pandas as pd
from django.db import transaction
from django.utils import timezone

from splitly.models import (
    User, Group, Membership, ExpenseCycle, ExpenseCycleMember,
    Expense, ExpenseParticipant, Settlement, CSVImport, ImportLog
)
from splitly.currency import convert_to_inr, get_rate, SUPPORTED_CURRENCIES
from splitly.split_engine import calculate_splits

# ─── Constants ────────────────────────────────────────────────────────────────

REQUIRED_FIELDS = ['title', 'amount', 'currency', 'date', 'paid_by_email', 'split_type']

VALID_SPLIT_TYPES = ['equal', 'unequal', 'percentage', 'share']

SETTLEMENT_KEYWORDS = re.compile(
    r'\b(settlement|settle|paid back|pay back|payback|reimburse|reimbursement|cleared)\b',
    re.IGNORECASE
)
REFUND_KEYWORDS = re.compile(
    r'\b(refund|return|credit|cashback|reversal)\b',
    re.IGNORECASE
)


# ─── Step 1: Parse CSV ────────────────────────────────────────────────────────

def parse_csv(file_obj) -> list[dict]:
    """
    Reads a CSV file object (InMemoryUploadedFile or path) using pandas.
    Returns a list of row dicts (raw strings — validation happens separately).
    Raises ValueError if the file is not a valid CSV or is empty.
    """
    try:
        if hasattr(file_obj, 'read'):
            content = file_obj.read()
            if isinstance(content, bytes):
                content = content.decode('utf-8-sig')  # handle BOM
            df = pd.read_csv(io.StringIO(content), dtype=str, keep_default_na=False)
        else:
            df = pd.read_csv(file_obj, dtype=str, keep_default_na=False)
    except Exception as e:
        raise ValueError(f"Could not parse CSV file: {e}")

    if df.empty:
        raise ValueError("CSV file is empty.")

    # Normalize column names
    df.columns = [c.strip().lower().replace(' ', '_') for c in df.columns]

    rows = []
    for i, row in df.iterrows():
        rows.append({k: str(v).strip() for k, v in row.items()})
    return rows


# ─── Step 2: Validate + Annotate Rows ────────────────────────────────────────

def validate_rows(import_session: CSVImport, raw_rows: list[dict], group: Group) -> list[ImportLog]:
    """
    For each raw row:
      - Checks all 11 anomaly categories
      - Creates ImportLog records in DB
    Returns the list of created ImportLog objects.
    All anomalies get status='anomaly'; clean rows get status='valid'.
    """
    active_member_emails = {u.email.lower() for u in group.get_active_members()}
    cycle = group.current_cycle

    import_rows = []
    duplicate_fingerprints = set()  # track within-file duplicates

    for idx, raw in enumerate(raw_rows, start=1):
        anomaly_type = 'NONE'
        explanation = ''
        suggested = ''
        row_status = 'valid'

        # ── 1. Missing Required Values ────────────────────────────────────────
        missing = [f for f in REQUIRED_FIELDS if not raw.get(f, '').strip()]
        if missing:
            anomaly_type = 'MISSING_VALUES'
            explanation = f"Row {idx} is missing required field(s): {', '.join(missing)}."
            suggested = "Fill in the missing values and re-upload, or reject this row."
            row_status = 'anomaly'

        # ── 2. Invalid Amount ─────────────────────────────────────────────────
        elif not _is_valid_decimal(raw.get('amount', '')):
            anomaly_type = 'MISSING_VALUES'
            explanation = f"Row {idx}: 'amount' value '{raw.get('amount')}' is not a valid number."
            suggested = "Correct the amount field. Must be a numeric value."
            row_status = 'anomaly'

        else:
            amount = Decimal(raw['amount'])

            # ── 3. Zero Amount ────────────────────────────────────────────────
            if amount == Decimal('0'):
                anomaly_type = 'ZERO_AMOUNT'
                explanation = f"Row {idx}: Amount is zero. Zero-value expenses have no financial effect."
                suggested = "Skip this row (recommended) or import anyway."
                row_status = 'anomaly'

            # ── 4. Negative Amount → check for refund or plain negative ──────
            elif amount < Decimal('0'):
                title = raw.get('title', '')
                if REFUND_KEYWORDS.search(title) or REFUND_KEYWORDS.search(raw.get('description', '')):
                    anomaly_type = 'REFUND'
                    explanation = (
                        f"Row {idx}: Detected a possible refund/return entry "
                        f"(title: '{title}', amount: {amount})."
                    )
                    suggested = "Approve to import as a refund expense, or reject to skip."
                else:
                    anomaly_type = 'NEGATIVE_AMOUNT'
                    explanation = (
                        f"Row {idx}: Amount {amount} is negative. "
                        "Negative amounts are unusual unless this is a refund/credit."
                    )
                    suggested = "Approve if this is intentional (e.g. a credit), or reject to skip."
                row_status = 'anomaly'

            # ── 5. Settlement Keyword Detection ───────────────────────────────
            elif SETTLEMENT_KEYWORDS.search(raw.get('title', '')) or \
                 SETTLEMENT_KEYWORDS.search(raw.get('description', '')):
                anomaly_type = 'SETTLEMENT'
                explanation = (
                    f"Row {idx}: Title/description suggests this is a settlement "
                    f"payment ('{raw.get('title')}'). Settlements should be stored "
                    "separately from expenses."
                )
                suggested = "Approve to import as a Settlement record instead of an Expense."
                row_status = 'anomaly'

            # ── 6. Invalid Currency ───────────────────────────────────────────
            elif raw.get('currency', '').upper() not in SUPPORTED_CURRENCIES:
                anomaly_type = 'INVALID_CURRENCY'
                explanation = (
                    f"Row {idx}: Currency '{raw.get('currency')}' is not supported. "
                    f"Supported: {', '.join(SUPPORTED_CURRENCIES)}."
                )
                suggested = "Correct the currency code and re-upload, or reject this row."
                row_status = 'anomaly'

            # ── 7. Invalid Date ───────────────────────────────────────────────
            elif not _is_valid_date(raw.get('date', '')):
                anomaly_type = 'INVALID_DATE'
                explanation = (
                    f"Row {idx}: Date '{raw.get('date')}' could not be parsed. "
                    "Expected formats: YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY."
                )
                suggested = "Correct the date value and re-upload, or reject this row."
                row_status = 'anomaly'

            # ── 8. Future Date ────────────────────────────────────────────────
            elif _parse_date(raw.get('date', '')) and _parse_date(raw['date']) > date.today():
                anomaly_type = 'INVALID_DATE'
                explanation = (
                    f"Row {idx}: Date '{raw['date']}' is in the future. "
                    "Expense dates should not exceed today."
                )
                suggested = "Approve to import anyway, or reject this row."
                row_status = 'anomaly'

            # ── 9. Participant / Payer Error ──────────────────────────────────
            elif raw.get('paid_by_email', '').lower() not in active_member_emails:
                anomaly_type = 'PARTICIPANT_ERROR'
                explanation = (
                    f"Row {idx}: Payer '{raw.get('paid_by_email')}' is not an active "
                    f"member of group '{group.name}'."
                )
                suggested = "Add this user to the group first, or reject this row."
                row_status = 'anomaly'

            # ── 10. Split Type Error ──────────────────────────────────────────
            elif raw.get('split_type', '').lower() not in VALID_SPLIT_TYPES:
                anomaly_type = 'SPLIT_TYPE_ERROR'
                explanation = (
                    f"Row {idx}: Split type '{raw.get('split_type')}' is invalid. "
                    f"Valid options: {', '.join(VALID_SPLIT_TYPES)}."
                )
                suggested = "Change to a valid split type and re-upload, or reject."
                row_status = 'anomaly'

            # ── 11. Member Conflict (listed participant not in cycle) ─────────
            elif raw.get('participants'):
                participant_emails = [e.strip().lower() for e in raw['participants'].split(';') if e.strip()]
                outsiders = [e for e in participant_emails if e not in active_member_emails]
                if outsiders:
                    anomaly_type = 'MEMBER_CONFLICT'
                    explanation = (
                        f"Row {idx}: Participant(s) not currently in group cycle: "
                        f"{', '.join(outsiders)}. They may have joined/left before this date."
                    )
                    suggested = "Approve to import with only current members, or reject."
                    row_status = 'anomaly'

            # ── 12. Duplicate Detection ───────────────────────────────────────
            if row_status == 'valid':
                fingerprint = (
                    raw.get('title', '').lower(),
                    raw.get('amount', ''),
                    raw.get('date', ''),
                    raw.get('paid_by_email', '').lower()
                )
                # Check within-file duplicates
                if fingerprint in duplicate_fingerprints:
                    anomaly_type = 'DUPLICATE'
                    explanation = (
                        f"Row {idx}: This entry appears more than once in the CSV file "
                        f"(title='{raw.get('title')}', amount={raw.get('amount')}, "
                        f"date={raw.get('date')}, payer={raw.get('paid_by_email')})."
                    )
                    suggested = "Reject the duplicate, or approve to import both copies."
                    row_status = 'anomaly'
                else:
                    duplicate_fingerprints.add(fingerprint)

                    # Check DB duplicates
                    if _is_db_duplicate(raw, group):
                        anomaly_type = 'DUPLICATE'
                        explanation = (
                            f"Row {idx}: A matching expense already exists in the database "
                            f"(title='{raw.get('title')}', amount={raw.get('amount')}, "
                            f"date={raw.get('date')})."
                        )
                        suggested = "Reject to skip this duplicate, or approve to force-import."
                        row_status = 'anomaly'

        csv_row = ImportLog.objects.create(
            import_session=import_session,
            row_number=idx,
            raw_data=raw,
            status=row_status,
            anomaly_type=anomaly_type,
            anomaly_explanation=explanation,
            suggested_action=suggested,
            user_decision='approve' if row_status == 'valid' else 'pending',
        )
        import_rows.append(csv_row)

    # Update session counters
    valid = sum(1 for r in import_rows if r.status == 'valid')
    anomaly = sum(1 for r in import_rows if r.status == 'anomaly')
    dup = sum(1 for r in import_rows if r.anomaly_type == 'DUPLICATE')
    import_session.total_rows = len(import_rows)
    import_session.valid_rows = valid
    import_session.invalid_rows = anomaly
    import_session.duplicate_rows = dup
    import_session.status = 'reviewing'
    import_session.save()

    return import_rows


# ─── Step 3: Execute Import ───────────────────────────────────────────────────

@transaction.atomic
def execute_import(import_session: CSVImport, requesting_user: User) -> dict:
    """
    Imports all rows where user_decision == 'approve'.
    Rows with 'reject' or still 'pending' are skipped.
    Returns a summary dict.
    """
    group = import_session.group
    cycle = group.current_cycle
    if not cycle:
        raise ValueError("No active expense cycle found for this group.")

    approved_rows = import_session.rows.filter(user_decision='approve')
    imported = 0
    skipped = 0
    settlements_created = 0
    errors = []

    for csv_row in approved_rows:
        raw = csv_row.raw_data
        try:
            if csv_row.anomaly_type == 'SETTLEMENT':
                _import_as_settlement(raw, group, cycle, requesting_user)
                settlements_created += 1
            else:
                _import_as_expense(raw, group, cycle, requesting_user)
            csv_row.status = 'imported'
            csv_row.save()
            imported += 1
        except Exception as e:
            errors.append(f"Row {csv_row.row_number}: {e}")
            csv_row.status = 'skipped'
            csv_row.decision_note = f"Import error: {e}"
            csv_row.save()
            skipped += 1

    # Mark rejected rows as skipped
    rejected_rows = import_session.rows.filter(user_decision='reject')
    for r in rejected_rows:
        r.status = 'skipped'
        r.save()
        skipped += 1

    # Update session
    import_session.status = 'completed'
    import_session.imported_rows = imported
    import_session.skipped_rows = skipped
    import_session.save()

    return {
        'imported': imported,
        'settlements_created': settlements_created,
        'skipped': skipped,
        'errors': errors,
    }


# ─── Step 4: Report Builder ───────────────────────────────────────────────────

def build_report(import_session: CSVImport) -> dict:
    """Returns a comprehensive import report dict for rendering."""
    rows = import_session.rows.all()
    anomaly_breakdown = {}
    for r in rows:
        if r.anomaly_type != 'NONE':
            anomaly_breakdown[r.anomaly_type] = anomaly_breakdown.get(r.anomaly_type, 0) + 1

    actions_taken = []
    for r in rows.filter(status='imported'):
        actions_taken.append({
            'row': r.row_number,
            'title': r.raw_data.get('title', '—'),
            'amount': r.raw_data.get('amount', '—'),
            'currency': r.raw_data.get('currency', '—'),
            'type': 'Settlement' if r.anomaly_type == 'SETTLEMENT' else 'Expense',
        })

    return {
        'session': import_session,
        'total_rows': import_session.total_rows,
        'valid_rows': import_session.valid_rows,
        'invalid_rows': import_session.invalid_rows,
        'duplicate_rows': import_session.duplicate_rows,
        'imported_rows': import_session.imported_rows,
        'skipped_rows': import_session.skipped_rows,
        'anomaly_breakdown': anomaly_breakdown,
        'actions_taken': actions_taken,
        'errors': [r.decision_note for r in rows.filter(status='skipped') if r.decision_note],
    }


# ─── Internal Helpers ─────────────────────────────────────────────────────────

def _is_valid_decimal(value: str) -> bool:
    try:
        Decimal(value)
        return True
    except (InvalidOperation, ValueError):
        return False


def _is_valid_date(value: str) -> bool:
    return _parse_date(value) is not None


def _parse_date(value: str):
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%d.%m.%Y'):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _is_db_duplicate(raw: dict, group: Group) -> bool:
    """Checks if a matching Expense already exists in the DB for this group."""
    title = raw.get('title', '').strip()
    amount_str = raw.get('amount', '0')
    date_val = _parse_date(raw.get('date', ''))

    if not date_val or not _is_valid_decimal(amount_str):
        return False

    try:
        amount = Decimal(amount_str)
        from splitly.pdf_engine import detect_duplicates
        return detect_duplicates(title, amount, date_val, group)
    except Exception:
        return False


def _import_as_expense(raw: dict, group: Group, cycle: ExpenseCycle, requesting_user: User):
    """Creates an Expense and ExpenseSplit records from a validated CSV row."""
    from splitly.currency import convert_to_inr, get_rate

    title = raw['title']
    amount = Decimal(raw['amount'])
    currency = raw['currency'].upper()
    date_val = _parse_date(raw['date'])
    category = raw.get('category', 'Other')
    split_type = raw.get('split_type', 'equal').lower()
    payer = User.objects.get(email__iexact=raw['paid_by_email'].strip())
    description = raw.get('description', '')
    location = raw.get('location', '')

    # Currency conversion
    rate = get_rate(currency)
    inr_val = convert_to_inr(amount, currency)

    # Determine participants
    participant_emails = [e.strip().lower() for e in raw.get('participants', '').split(';') if e.strip()]
    if participant_emails:
        participants = list(User.objects.filter(
            email__in=participant_emails,
            group_memberships__group=group,
            group_memberships__status='active'
        ).distinct())
    else:
        participants = list(cycle.members.all())

    if not participants:
        participants = list(cycle.members.all())

    # Build split inputs
    inputs = {}
    for mem in participants:
        key = f'split_val_{mem.id}'
        inputs[mem.id] = raw.get(key, '0') or '0'

    splits, breakdown = calculate_splits(abs(amount), split_type, participants, inputs)

    expense = Expense.objects.create(
        cycle=cycle,
        title=title,
        amount=amount,
        currency=currency,
        exchange_rate=rate,
        converted_inr_value=inr_val,
        date=date_val,
        category=category if category in dict(Expense.CATEGORY_CHOICES) else 'Other',
        paid_by=payer,
        split_type=split_type,
        location=location,
        description=description,
        created_by=requesting_user,
        imported_from_csv=True,
    )

    for member, split_amount in splits.items():
        split_inr = convert_to_inr(split_amount, currency)
        ExpenseParticipant.objects.create(
            expense=expense,
            user=member,
            amount=split_amount,
            amount_inr=split_inr,
        )


def _import_as_settlement(raw: dict, group: Group, cycle: ExpenseCycle, requesting_user: User):
    """Creates a Settlement record from a CSV row flagged as a settlement."""
    from splitly.currency import convert_to_inr, get_rate

    amount = Decimal(raw.get('amount', '0'))
    currency = raw.get('currency', 'INR').upper()
    date_val = _parse_date(raw.get('date', '')) or date.today()
    payer = User.objects.get(email__iexact=raw['paid_by_email'].strip())

    # Try to infer receiver from participants
    participant_emails = [e.strip().lower() for e in raw.get('participants', '').split(';') if e.strip()]
    receiver = None
    for email in participant_emails:
        if email.lower() != payer.email.lower():
            try:
                receiver = User.objects.get(email__iexact=email)
                break
            except User.DoesNotExist:
                continue

    if not receiver:
        raise ValueError(
            f"Cannot determine settlement receiver. "
            "Please list receiver email in the 'participants' column."
        )

    rate = get_rate(currency)
    inr_val = convert_to_inr(amount, currency)

    Settlement.objects.create(
        group=group,
        cycle=cycle,
        payer=payer,
        receiver=receiver,
        amount=amount,
        currency=currency,
        exchange_rate=rate,
        converted_inr_value=inr_val,
        date=date_val,
        description=raw.get('description', f"CSV import: {raw.get('title', '')}"),
        created_by=requesting_user,
    )
