"""
pdf_engine.py — PDF Import Engine for Splitly (Phase 1 & 2 Expansion).

Pipeline (mirrors csv_engine.py):
  1. parse_pdf()               → extract raw text from uploaded PDF
  2. extract_expenses_from_text() → AI-lite: regex + keyword parsing → list of row dicts
  3. validate_pdf_rows()       → anomaly detection, member timeline checks, duplicate detection
  4. execute_pdf_import()      → commit approved rows to DB
  5. build_pdf_report()        → return summary stats dict

Also provides:
  - detect_duplicates()        → shared duplicate detection (used by both CSV and PDF engines)
  - match_receipt_to_expense() → match a receipt image/PDF to an existing expense
  - ai_suggest_category()      → keyword-based automatic category classification
"""

import re
import io
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone

from splitly.models import (
    User, Group, Membership, ExpenseCycle, ExpenseCycleMember,
    Expense, ExpenseParticipant, PDFImport, ImportLog, Settlement
)
from splitly.currency import convert_to_inr, get_rate
from splitly.split_engine import calculate_splits

# ─── Constants ────────────────────────────────────────────────────────────────

SUPPORTED_CURRENCIES_LIST = ['INR', 'USD', 'EUR', 'GBP', 'AED']

# AI Category keyword mapping — order matters (first match wins)
CATEGORY_KEYWORDS = [
    ('Food',          re.compile(r'\b(food|lunch|dinner|breakfast|restaurant|cafe|coffee|snack|meal|groceries?|swiggy|zomato|dominos|pizza|burger|biryani|hotel|canteen|tiffin|bakery|juice)\b', re.I)),
    ('Travel',        re.compile(r'\b(uber|ola|cab|taxi|flight|train|bus|metro|auto|petrol|fuel|toll|parking|rapido|travel|airport|railway|irctc|redbus|makemytrip|goibibo|airline|booking)\b', re.I)),
    ('Rent',          re.compile(r'\b(rent|lease|pgroom|hostel|society|maintenance|deposit|flat|apartment|room)\b', re.I)),
    ('Shopping',      re.compile(r'\b(amazon|flipkart|myntra|shopping|clothes|shirt|shoes|bag|dress|meesho|ajio|nykaa|cart|order|purchase|buy|cloth)\b', re.I)),
    ('Electricity',   re.compile(r'\b(electricity|electric|power|bill|water|gas|internet|broadband|wifi|phone|recharge|mobile|bsnl|jio|airtel|vi|postpaid|prepaid|dth)\b', re.I)),
    ('Entertainment', re.compile(r'\b(movie|cinema|netflix|amazon prime|hotstar|spotify|game|concert|event|ticket|show|theatre|outing|party|pub|bar|club)\b', re.I)),
    ('Medical',       re.compile(r'\b(hospital|pharmacy|medicine|doctor|clinic|medical|health|apollo|lab|test|scan|consultation|chemist|diagnostic)\b', re.I)),
    ('Salary',        re.compile(r'\b(salary|payroll|advance|bonus|stipend|wage)\b', re.I)),
]

# Amount extraction patterns — handles ₹1,200.50 / USD 45.00 / $200 / 1200 INR
AMOUNT_PATTERNS = [
    re.compile(r'(?:₹|INR)\s*([\d,]+(?:\.\d{1,2})?)', re.I),
    re.compile(r'(?:USD|\$)\s*([\d,]+(?:\.\d{1,2})?)', re.I),
    re.compile(r'(?:EUR|€)\s*([\d,]+(?:\.\d{1,2})?)', re.I),
    re.compile(r'(?:GBP|£)\s*([\d,]+(?:\.\d{1,2})?)', re.I),
    re.compile(r'(?:AED)\s*([\d,]+(?:\.\d{1,2})?)', re.I),
    re.compile(r'([\d,]+(?:\.\d{1,2})?)\s*(?:INR|USD|EUR|GBP|AED)', re.I),
    re.compile(r'([\d,]+\.\d{2})\b'),  # fallback: decimal numbers
]
CURRENCY_PATTERNS = [
    (re.compile(r'(?:₹|INR)', re.I), 'INR'),
    (re.compile(r'(?:USD|\$)', re.I), 'USD'),
    (re.compile(r'(?:EUR|€)', re.I), 'EUR'),
    (re.compile(r'(?:GBP|£)', re.I), 'GBP'),
    (re.compile(r'\bAED\b', re.I), 'AED'),
]

# Date extraction patterns
DATE_PATTERNS = [
    (re.compile(r'\b(\d{4}[-/]\d{2}[-/]\d{2})\b'), '%Y-%m-%d'),
    (re.compile(r'\b(\d{2}[-/]\d{2}[-/]\d{4})\b'), '%d-%m-%Y'),
    (re.compile(r'\b(\d{2}[-/]\d{2}[-/]\d{2})\b'), '%d-%m-%y'),
    (re.compile(r'\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b', re.I), '%d %b %Y'),
    (re.compile(r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b', re.I), '%b %d %Y'),
]

SETTLEMENT_KEYWORDS = re.compile(r'\b(settlement|settle|paid back|pay back|payback|reimburse|reimbursement|cleared|repaid)\b', re.I)
REFUND_KEYWORDS = re.compile(r'\b(refund|return|credit|cashback|reversal)\b', re.I)


# ─── AI Category Suggestion ───────────────────────────────────────────────────

def ai_suggest_category(text: str) -> str:
    """
    Given a text string (expense title, description, merchant name),
    returns the best-matching expense category using keyword rules.
    Returns 'Other' if no match found.
    """
    if not text:
        return 'Other'
    for category, pattern in CATEGORY_KEYWORDS:
        if pattern.search(text):
            return category
    return 'Other'


# ─── Step 1: Parse PDF ────────────────────────────────────────────────────────

def parse_pdf(file_obj) -> str:
    """
    Extracts all text from a PDF file using pdfplumber.
    Returns the full text as a string.
    Raises ValueError if the file cannot be read or is empty.
    """
    try:
        import pdfplumber
        if hasattr(file_obj, 'read'):
            content = file_obj.read()
            pdf_io = io.BytesIO(content)
        else:
            with open(file_obj, 'rb') as f:
                content = f.read()
            pdf_io = io.BytesIO(content)

        all_text = []
        with pdfplumber.open(pdf_io) as pdf:
            if len(pdf.pages) == 0:
                raise ValueError("The uploaded PDF has no pages.")
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    all_text.append(text)

        full_text = '\n'.join(all_text).strip()
        if not full_text:
            raise ValueError("Could not extract any text from this PDF. It may be image-only or password-protected.")
        return full_text

    except ImportError:
        raise ValueError("PDF processing library (pdfplumber) is not installed. Run: pip install pdfplumber")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to read PDF: {str(e)}")


# ─── Step 2: AI-Lite Expense Extraction ──────────────────────────────────────

def _extract_amount_and_currency(line: str):
    """Extract the first amount and its currency from a line of text."""
    # Try currency-tagged patterns first
    for pat, sym_pattern, currency in [
        (AMOUNT_PATTERNS[0], None, 'INR'),
        (AMOUNT_PATTERNS[1], None, 'USD'),
        (AMOUNT_PATTERNS[2], None, 'EUR'),
        (AMOUNT_PATTERNS[3], None, 'GBP'),
        (AMOUNT_PATTERNS[4], None, 'AED'),
    ]:
        m = pat.search(line)
        if m:
            try:
                amount = Decimal(m.group(1).replace(',', ''))
                if amount > 0:
                    return amount, currency
            except Exception:
                pass

    # Try suffix patterns (e.g. "1200 INR")
    suffix_pat = AMOUNT_PATTERNS[5]
    m = suffix_pat.search(line)
    if m:
        raw_num = m.group(1).replace(',', '')
        # Find currency in line
        currency = 'INR'
        for cpat, curr in CURRENCY_PATTERNS:
            if cpat.search(line):
                currency = curr
                break
        try:
            amount = Decimal(raw_num)
            if amount > 0:
                return amount, currency
        except Exception:
            pass

    # Fallback: any decimal number
    m = AMOUNT_PATTERNS[6].search(line)
    if m:
        try:
            amount = Decimal(m.group(1).replace(',', ''))
            if 0 < amount < 10_000_000:
                return amount, 'INR'
        except Exception:
            pass

    return None, None


def _extract_date(line: str):
    """Extract the first parseable date from a line."""
    for pat, fmt in DATE_PATTERNS:
        m = pat.search(line)
        if m:
            raw = m.group(1).strip().rstrip(',')
            # Normalise separators
            raw = raw.replace('/', '-')
            try:
                fmt_norm = fmt.replace('/', '-')
                return datetime.strptime(raw, fmt_norm).date()
            except ValueError:
                try:
                    # Try common alternative formats
                    for alt_fmt in ['%d-%m-%Y', '%Y-%m-%d', '%d-%b-%Y', '%b-%d-%Y']:
                        try:
                            return datetime.strptime(raw, alt_fmt).date()
                        except ValueError:
                            pass
                except Exception:
                    pass
    return None


def extract_expenses_from_text(text: str) -> list[dict]:
    """
    AI-lite extraction: parses lines of PDF text and identifies expense entries.
    Returns a list of raw row dicts (partial — may be missing some fields).

    Heuristic: A line is considered an expense candidate if it contains:
    - An amount (numeric value with optional currency symbol)
    - Some descriptive text (title)
    """
    rows = []
    lines = text.split('\n')

    for i, line in enumerate(lines):
        line = line.strip()
        if len(line) < 5:
            continue

        amount, currency = _extract_amount_and_currency(line)
        if amount is None:
            continue

        # Skip header-like lines
        if re.match(r'^(date|amount|total|balance|description|particulars|narration|s\.?\s*no|#)', line, re.I):
            continue

        # Try to extract date — look at this line first, then fallback to nearby context
        context = ' '.join(lines[max(0, i-1):i+2])
        expense_date = _extract_date(line) or _extract_date(context) or date.today()

        # Extract title: remove amounts, dates, currency symbols from line
        title = line
        title = re.sub(r'(?:₹|INR|USD|\$|EUR|€|GBP|£|AED)\s*[\d,]+(?:\.\d{1,2})?', '', title, flags=re.I)
        title = re.sub(r'[\d,]+(?:\.\d{1,2})?\s*(?:INR|USD|EUR|GBP|AED)', '', title, flags=re.I)
        title = re.sub(r'\b\d{4}[-/]\d{2}[-/]\d{2}\b', '', title)
        title = re.sub(r'\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b', '', title)
        title = re.sub(r'\s+', ' ', title).strip(' -|:,.')

        if not title or len(title) < 3:
            title = f"Extracted Expense {len(rows) + 1}"

        # AI category suggestion
        suggested_category = ai_suggest_category(f"{title} {line}")

        # Settlement / refund detection
        is_settlement = bool(SETTLEMENT_KEYWORDS.search(line))
        is_refund = bool(REFUND_KEYWORDS.search(line))

        rows.append({
            'title': title[:100],
            'amount': str(amount),
            'currency': currency,
            'date': expense_date.strftime('%Y-%m-%d'),
            'category': suggested_category,
            'ai_suggested_category': suggested_category,
            'split_type': 'equal',
            'paid_by_email': '',  # To be filled by user during review
            'description': f"Extracted from PDF. Original line: {line[:200]}",
            '_is_settlement': is_settlement,
            '_is_refund': is_refund,
            '_source_line': line[:200],
        })

    return rows


# ─── Duplicate Detection (shared by CSV and PDF engines) ─────────────────────

def detect_duplicates(title: str, amount: Decimal, exp_date: date, group: 'Group') -> bool:
    """
    Checks if a very similar expense already exists in the group.
    Returns True if a likely duplicate is found.

    Criteria: same group + amount within ±1 + date within ±3 days + title similarity ≥60%
    """
    date_from = exp_date - timedelta(days=3)
    date_to = exp_date + timedelta(days=3)
    amount_low = amount * Decimal('0.99')
    amount_high = amount * Decimal('1.01')

    cycles = group.cycles.all()
    candidates = Expense.objects.filter(
        cycle__in=cycles,
        is_deleted=False,
        date__gte=date_from,
        date__lte=date_to,
        amount__gte=amount_low,
        amount__lte=amount_high,
    )

    if not candidates.exists():
        return False

    # Simple title similarity: check if ≥50% of words overlap
    title_words = set(re.findall(r'\w+', title.lower()))
    if not title_words:
        return True  # amount+date match is enough

    for cand in candidates:
        cand_words = set(re.findall(r'\w+', cand.title.lower()))
        if not cand_words:
            continue
        overlap = len(title_words & cand_words) / max(len(title_words), len(cand_words))
        if overlap >= 0.5:
            return True

    return False


# ─── Step 3: Validate PDF Rows ────────────────────────────────────────────────

def validate_pdf_rows(session: 'PDFImport', rows: list[dict], group: 'Group'):
    """
    Creates ImportLog records for each extracted row, with anomaly detection.
    Mirrors csv_engine.validate_rows().
    """
    valid_count = 0
    anomaly_count = 0

    for i, row in enumerate(rows, start=1):
        anomaly_type = 'NONE'
        anomaly_explanation = ''
        suggested_action = ''
        status = 'valid'

        # --- Amount validation ---
        try:
            amount = Decimal(str(row.get('amount', 0)))
            if amount <= 0:
                anomaly_type = 'ZERO_AMOUNT'
                anomaly_explanation = f"Amount is zero or negative: {amount}."
                suggested_action = "Reject unless this is intentional."
                status = 'anomaly'
        except InvalidOperation:
            anomaly_type = 'MISSING_VALUES'
            anomaly_explanation = "Could not parse amount from PDF text."
            suggested_action = "Reject this row or manually enter the expense."
            status = 'anomaly'
            amount = Decimal('0')

        # --- Currency validation ---
        if status == 'valid':
            currency = row.get('currency', 'INR').upper()
            if currency not in SUPPORTED_CURRENCIES_LIST:
                anomaly_type = 'INVALID_CURRENCY'
                anomaly_explanation = f"Currency '{currency}' is not supported."
                suggested_action = "Reject or correct the currency."
                status = 'anomaly'

        # --- Date validation ---
        if status == 'valid':
            try:
                exp_date = datetime.strptime(row.get('date', ''), '%Y-%m-%d').date()
                today = date.today()
                if exp_date > today:
                    anomaly_type = 'INVALID_DATE'
                    anomaly_explanation = f"Date {exp_date} is in the future."
                    suggested_action = "Approve if this is pre-scheduled, otherwise reject."
                    status = 'anomaly'
            except (ValueError, TypeError):
                anomaly_type = 'INVALID_DATE'
                anomaly_explanation = f"Could not parse date: {row.get('date')}."
                suggested_action = "Reject or manually set the date."
                status = 'anomaly'
                exp_date = date.today()

        # --- Settlement / Refund detection ---
        if status == 'valid' and row.get('_is_settlement'):
            anomaly_type = 'SETTLEMENT'
            anomaly_explanation = "This entry looks like a settlement payment, not a shared expense."
            suggested_action = "Reject and record this as a Settlement instead."
            status = 'anomaly'

        if status == 'valid' and row.get('_is_refund'):
            anomaly_type = 'REFUND'
            anomaly_explanation = "This entry may be a refund or credit."
            suggested_action = "Reject if this is a refund."
            status = 'anomaly'

        # --- Duplicate detection ---
        if status == 'valid':
            try:
                exp_date_obj = datetime.strptime(row.get('date', ''), '%Y-%m-%d').date()
                if detect_duplicates(row.get('title', ''), amount, exp_date_obj, group):
                    anomaly_type = 'DUPLICATE'
                    anomaly_explanation = "A very similar expense (same amount, nearby date, similar title) already exists in this group."
                    suggested_action = "Reject to avoid double-counting, unless this is a new occurrence."
                    status = 'anomaly'
            except Exception:
                pass

        if status == 'valid':
            valid_count += 1
        else:
            anomaly_count += 1

        # Clean internal flags from raw_data before saving
        raw_data = {k: v for k, v in row.items() if not k.startswith('_')}

        ImportLog.objects.create(
            import_session=None,
            pdf_import_session=session,
            row_number=i,
            raw_data=raw_data,
            status=status,
            anomaly_type=anomaly_type,
            anomaly_explanation=anomaly_explanation,
            suggested_action=suggested_action,
            source='pdf',
        )

    session.total_rows = len(rows)
    session.valid_rows = valid_count
    session.invalid_rows = anomaly_count
    session.save()


# ─── Step 4: Execute PDF Import ───────────────────────────────────────────────

@transaction.atomic
def execute_pdf_import(session: 'PDFImport', requesting_user: 'User') -> dict:
    """
    Imports all approved/valid rows from a PDF import session.
    Mirrors csv_engine.execute_import().
    """
    rows = session.pdf_rows.filter(status__in=['valid', 'anomaly'])
    imported = 0
    skipped = 0
    errors = []

    group = session.group
    cycle = group.current_cycle
    if not cycle:
        cycle = ExpenseCycle.objects.create(group=group, status='active')
        for m in group.get_active_members():
            ExpenseCycleMember.objects.create(cycle=cycle, user=m)

    cycle_members = list(cycle.members.all())
    member_map = {m.email: m for m in cycle_members}

    for row_obj in rows:
        # Skip anomalies that user rejected
        if row_obj.status == 'anomaly' and row_obj.user_decision == 'reject':
            row_obj.status = 'skipped'
            row_obj.save()
            skipped += 1
            continue
        # Skip anomalies still pending (safety check)
        if row_obj.status == 'anomaly' and row_obj.user_decision == 'pending':
            skipped += 1
            continue

        raw = row_obj.raw_data
        try:
            amount = Decimal(str(raw.get('amount', 0)))
            currency = raw.get('currency', 'INR').upper()
            exp_date = datetime.strptime(raw.get('date', ''), '%Y-%m-%d').date()
            title = raw.get('title', 'PDF Import')
            category = raw.get('category', 'Other')
            ai_category = raw.get('ai_suggested_category', category)

            # paid_by: use requesting user as default if not filled
            paid_by_email = raw.get('paid_by_email', '').strip()
            paid_by = member_map.get(paid_by_email, requesting_user)

            rate = get_rate(currency)
            inr_value = convert_to_inr(amount, currency)

            expense = Expense.objects.create(
                cycle=cycle,
                title=title,
                amount=amount,
                currency=currency,
                exchange_rate=rate,
                converted_inr_value=inr_value,
                date=exp_date,
                category=category,
                ai_suggested_category=ai_category,
                paid_by=paid_by,
                split_type='equal',
                description=raw.get('description', ''),
                created_by=requesting_user,
                imported_from_pdf=True,
            )

            # Equal split among all cycle members
            split_amount = (amount / len(cycle_members)).quantize(Decimal('0.01'))
            for member in cycle_members:
                ExpenseParticipant.objects.create(
                    expense=expense,
                    user=member,
                    amount=split_amount,
                    amount_inr=convert_to_inr(split_amount, currency),
                    input_value=split_amount,
                )

            row_obj.status = 'imported'
            row_obj.save()
            imported += 1

        except Exception as e:
            errors.append(f"Row {row_obj.row_number}: {str(e)}")
            skipped += 1

    session.status = 'completed'
    session.imported_rows = imported
    session.skipped_rows = skipped
    session.save()

    return {
        'imported': imported,
        'skipped': skipped,
        'errors': errors,
    }


# ─── Step 5: Build PDF Report ─────────────────────────────────────────────────

def build_pdf_report(session: 'PDFImport') -> dict:
    """Returns a summary dict for the post-import report page."""
    rows = session.pdf_rows.all()
    return {
        'session': session,
        'total_rows': session.total_rows,
        'valid_rows': session.valid_rows,
        'invalid_rows': session.invalid_rows,
        'imported_rows': session.imported_rows,
        'skipped_rows': session.skipped_rows,
        'rows': rows,
        'imported_list': rows.filter(status='imported'),
        'skipped_list': rows.filter(status='skipped'),
        'anomaly_list': rows.filter(status='anomaly'),
    }


# ─── Receipt Matching ─────────────────────────────────────────────────────────

def match_receipt_to_expense(receipt_text: str, group: 'Group', days_window: int = 7) -> list:
    """
    Given extracted text from a receipt, finds candidate expenses in the group
    that might correspond to it (matching by amount and date proximity).
    Returns a list of (expense, confidence_score) tuples sorted by score desc.
    """
    amount, currency = _extract_amount_and_currency(receipt_text)
    exp_date = _extract_date(receipt_text) or date.today()

    if amount is None:
        return []

    date_from = exp_date - timedelta(days=days_window)
    date_to = exp_date + timedelta(days=days_window)
    amount_low = amount * Decimal('0.95')
    amount_high = amount * Decimal('1.05')

    cycles = group.cycles.all()
    candidates = Expense.objects.filter(
        cycle__in=cycles,
        is_deleted=False,
        date__gte=date_from,
        date__lte=date_to,
        amount__gte=amount_low,
        amount__lte=amount_high,
    ).select_related('paid_by')

    results = []
    for exp in candidates:
        score = 0
        # Exact amount match
        if exp.amount == amount:
            score += 50
        else:
            score += 30
        # Date proximity
        delta = abs((exp.date - exp_date).days)
        score += max(0, 20 - delta * 3)
        # Currency match
        if exp.currency == currency:
            score += 20
        # Title keyword overlap
        title_words = set(re.findall(r'\w+', receipt_text.lower()))
        exp_words = set(re.findall(r'\w+', exp.title.lower()))
        overlap = len(title_words & exp_words)
        score += min(overlap * 5, 30)

        results.append((exp, score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:5]  # top 5 candidates
