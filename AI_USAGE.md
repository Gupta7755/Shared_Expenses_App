# Splitly — AI Usage & Cases Log (AI_USAGE.md)

This log details the AI coding tools utilized during the construction of Splitly, typical prompts, and three concrete cases where the AI generated incorrect code, how it was identified, and the resulting corrections.

---

## 🤖 AI Tools & Prompts
* **AI Tool**: Gemini 3.5 / Antigravity coding assistant.
* **Key Prompts**:
  1. *Prompt*: "Create a local Python-based PDF parser using pdfplumber to extract transactions, detect amounts and dates, and suggest categories using regex mapping keyword search."
  2. *Prompt*: "Write a comprehensive unit test suite in Django to check PDF parsing, duplicate matching, auto-splits, and receipt matches."
  3. *Prompt*: "Review views.py and templates to fix the invalid filter 'abs' TemplateSyntaxError at /dashboard/."

---

## 🛠️ Case Studies of AI Errors & Corrections

### Case 1: UserManager `create_user` missing positional `username` parameter
* **What the AI produced**:
  The AI generated a unit test file ([tests.py](file:///c:/Users/rahul/Desktop/interview_project/Shared_Expenses_App/splitly/tests.py)) containing:
  ```python
  self.user1 = User.objects.create_user(email="alice@example.com", password="password", first_name="Alice")
  ```
* **How it was caught**:
  Running `python manage.py test` crashed during setup:
  ```text
  TypeError: UserManager.create_user() missing 1 required positional argument: 'username'
  ```
* **What was changed**:
  Even though the custom user model set `USERNAME_FIELD = 'email'`, the Django `UserManager` implementation still requires a `username` positional argument when calling `create_user()`. I updated the call to pass `username="alice@example.com"` explicitly.

---

### Case 2: Multi-line Context Date Collision
* **What the AI produced**:
  In [pdf_engine.py](file:///c:/Users/rahul/Desktop/interview_project/Shared_Expenses_App/splitly/pdf_engine.py), the date extraction heuristic was defined as:
  ```python
  context = ' '.join(lines[max(0, i-1):i+2])
  expense_date = _extract_date(context) or date.today()
  ```
* **How it was caught**:
  Running the test suite triggered an assertion failure in `test_extract_expenses_from_text`:
  ```text
  AssertionError: '2026-06-10' != '2026-06-11'
  ```
  The context-based extraction pulled the date from a neighboring line (`i-1`) instead of the transaction's own line (`i`) when parsing sequential transactions with distinct dates.
* **What was changed**:
  I modified the logic to look for a date on the current line (`line`) first, and only fall back to looking at the surrounding context if no date is found on the current line:
  ```python
  context = ' '.join(lines[max(0, i-1):i+2])
  expense_date = _extract_date(line) or _extract_date(context) or date.today()
  ```

---

### Case 3: Invalid `|abs` Django Template Filter
* **What the AI produced**:
  In [dashboard.html](file:///c:/Users/rahul/Desktop/interview_project/Shared_Expenses_App/splitly/templates/splitly/dashboard.html), the balance formatting logic was generated as:
  ```html
  -${{ item.balance|abs|floatformat:2 }}
  ```
* **How it was caught**:
  Visiting `http://127.0.0.1:8000/dashboard/` crashed the dev server with:
  ```text
  TemplateSyntaxError: Invalid filter: 'abs'
  ```
  Django does not have a built-in `abs` filter in its template language.
* **What was changed**:
  Instead of writing a custom filter registration, I updated the view context generator in [views.py](file:///c:/Users/rahul/Desktop/interview_project/Shared_Expenses_App/splitly/views.py) to compute the absolute balance (`'abs_balance': abs(user_bal)`) on the Python backend, and updated the template to reference this context variable:
  ```html
  -${{ item.abs_balance|floatformat:2 }}
  ```
