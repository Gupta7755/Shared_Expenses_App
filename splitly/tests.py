from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from splitly.models import Group, ExpenseCycle, ExpenseCycleMember, Expense, PDFImport, ImportLog
from splitly.pdf_engine import (
    ai_suggest_category, extract_expenses_from_text, detect_duplicates, match_receipt_to_expense
)
from splitly.split_engine import suggest_split_type

User = get_user_model()

class SplitlyEngineTests(TestCase):
    def setUp(self):
        # Create users
        self.user1 = User.objects.create_user(username="alice@example.com", email="alice@example.com", password="password", first_name="Alice")
        self.user2 = User.objects.create_user(username="bob@example.com", email="bob@example.com", password="password", first_name="Bob")
        
        # Create group
        self.group = Group.objects.create(name="Trip Group", created_by=self.user1)
        self.group.memberships.create(user=self.user1, status='active')
        self.group.memberships.create(user=self.user2, status='active')
        
        # Create cycle
        self.cycle = ExpenseCycle.objects.create(group=self.group, status='active')
        ExpenseCycleMember.objects.create(cycle=self.cycle, user=self.user1)
        ExpenseCycleMember.objects.create(cycle=self.cycle, user=self.user2)

    def test_ai_suggest_category(self):
        self.assertEqual(ai_suggest_category("lunch with team"), "Food")
        self.assertEqual(ai_suggest_category("uber ride to office"), "Travel")
        self.assertEqual(ai_suggest_category("monthly rent payment"), "Rent")
        self.assertEqual(ai_suggest_category("bought clothes on amazon"), "Shopping")
        self.assertEqual(ai_suggest_category("electricity bill"), "Electricity")
        self.assertEqual(ai_suggest_category("movie tickets"), "Entertainment")
        self.assertEqual(ai_suggest_category("hospital checkup"), "Medical")
        self.assertEqual(ai_suggest_category("random stuff"), "Other")

    def test_extract_expenses_from_text(self):
        text = "2026-06-10 Lunch at restaurant ₹1500.50\n2026-06-11 Uber ride USD 45.00\nRent flat 20000 INR"
        rows = extract_expenses_from_text(text)
        self.assertEqual(len(rows), 3)
        
        # Row 1
        self.assertEqual(rows[0]['title'], "Lunch at restaurant")
        self.assertEqual(rows[0]['amount'], "1500.50")
        self.assertEqual(rows[0]['currency'], "INR")
        self.assertEqual(rows[0]['category'], "Food")
        self.assertEqual(rows[0]['date'], "2026-06-10")
        
        # Row 2
        self.assertEqual(rows[1]['title'], "Uber ride")
        self.assertEqual(rows[1]['amount'], "45.00")
        self.assertEqual(rows[1]['currency'], "USD")
        self.assertEqual(rows[1]['category'], "Travel")
        self.assertEqual(rows[1]['date'], "2026-06-11")
        
        # Row 3
        self.assertEqual(rows[2]['title'], "Rent flat")
        self.assertEqual(rows[2]['amount'], "20000")
        self.assertEqual(rows[2]['currency'], "INR")
        self.assertEqual(rows[2]['category'], "Rent")

    def test_detect_duplicates(self):
        # Create an expense
        expense_date = date.today()
        expense = Expense.objects.create(
            cycle=self.cycle,
            title="Team Dinner",
            amount=Decimal("1200.00"),
            currency="INR",
            paid_by=self.user1,
            created_by=self.user1,
            date=expense_date
        )
        
        # Exact match
        is_dup = detect_duplicates("Team Dinner", Decimal("1200.00"), expense_date, self.group)
        self.assertTrue(is_dup)
        
        # Similar title, exact amount, date within window
        is_dup_similar = detect_duplicates("Our Dinner", Decimal("1200.00"), expense_date + timedelta(days=2), self.group)
        self.assertTrue(is_dup_similar)
        
        # Date outside window
        is_not_dup_date = detect_duplicates("Team Dinner", Decimal("1200.00"), expense_date - timedelta(days=5), self.group)
        self.assertFalse(is_not_dup_date)
        
        # Different amount
        is_not_dup_amt = detect_duplicates("Team Dinner", Decimal("1300.00"), expense_date, self.group)
        self.assertFalse(is_not_dup_amt)

    def test_suggest_split_type(self):
        # Initially no history -> default 'equal'
        self.assertEqual(suggest_split_type(self.user1, self.group), 'equal')
        
        # Create history with unequal splits
        for i in range(3):
            Expense.objects.create(
                cycle=self.cycle,
                title=f"Expense {i}",
                amount=Decimal("100.00"),
                currency="INR",
                paid_by=self.user1,
                created_by=self.user1,
                split_type='unequal'
            )
            
        # Payer 1 should now be suggested 'unequal'
        self.assertEqual(suggest_split_type(self.user1, self.group), 'unequal')
        # Payer 2 should still be 'equal'
        self.assertEqual(suggest_split_type(self.user2, self.group), 'equal')

    def test_match_receipt_to_expense(self):
        expense_date = date.today()
        expense = Expense.objects.create(
            cycle=self.cycle,
            title="Starbucks Coffee",
            amount=Decimal("350.00"),
            currency="INR",
            paid_by=self.user1,
            created_by=self.user1,
            date=expense_date
        )
        
        # Receipt text containing amount and date
        receipt_text = f"STARBUCKS COFFEE\nDATE: {expense_date.strftime('%Y-%m-%d')}\nTOTAL: ₹350.00\nTHANK YOU"
        candidates = match_receipt_to_expense(receipt_text, self.group)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][0], expense)
