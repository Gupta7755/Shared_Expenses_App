# Splitly — Smart Shared Expenses Web Application

**Splitly** is a premium Django-based multi-currency expense sharing web application (inspired by Splitwise) designed to simplify group expenses, automate balance settling, and provide smart financial analytics. 

It features automated PDF bank statement processing, heuristic-based AI expense classification, split-type prediction, live currency conversion, debt simplification algorithms, and interactive analytics dashboards.

---

## 🚀 Key Features

### 1. Smart Data Collection & PDF Ingestion
* **PDF Expense Ingestion**: Upload bank statement PDFs or invoices. The system automatically parses them using `pdfplumber` to extract transaction titles, dates, amounts, and currencies.
* **CSV Expense Ingestion**: Bulk-upload expenses using formatted CSV templates.
* **OCR & Heuristic AI Categorization**: Auto-classifies transactions into categories (Food, Travel, Rent, Shopping, Electricity, Entertainment, Medical, Other) using regular expression keyword patterns.
* **Auto-Split Suggestions**: Learns from a payer's historical spending habits inside a group to suggest the most likely split type (Equal, Unequal, Percentage, or Shares) upon adding an expense.
* **Receipt Image Matching**: Upload receipt images and match them to candidate database expenses via amount and date window proximity.

### 2. Smart Settlement & Balance Engine
* **Debt Simplification Algorithm**: Calculates the net balances of all group members and runs a simplification pass to minimize the total number of transaction payments required to settle the group cycle.
* **Multi-Currency Support**: Supports transactions in INR, USD, EUR, GBP, and AED with live or manually updated conversion rates to INR for centralized balance aggregation.
* **Dynamic Membership Timeline**: Ensures transactions are split only among members who were active in the group when the expense was recorded.

### 3. Analytics & Visualization
* **Spending Heatmap**: A GitHub-style daily calendar activity grid visualizing daily spend intensity over the past 12 weeks.
* **Member Contributions**: A radial breakdown of member contributions showing who has funded the group most.
* **AI Insights Panel**: A panel generating deterministic observations from live data (e.g., spending trends month-over-month, peak spending periods, foreign currency ratio analysis).
* **Exportable PDF & CSV Reports**:
  - **Individual Reports**: Membership timeline, expense logs, category breakdown, currency conversion, and final balances.
  - **Group Reports**: Full group summaries, final settlement instructions, and import histories.

### 4. Administration & Security
* **Interactive Admin Dashboard**: Full CRUD management of Users, Groups, Expenses, Settlements, and Audits.
* **Comprehensive Audit Log**: Tracks user log-ins, additions/updates/deletions, member changes, CSV/PDF imports, and report generations.
* **Notifications Engine**: In-app read/unread alerts for key group actions (e.g. member joins/leaves, expense additions, CSV/PDF imports).

---

## 🛠️ Technology Stack
* **Backend Framework**: Django 4.2+ (Python)
* **Data Processing**: pandas (used for calculating cycle balances, matrix operations, and CSV generation)
* **Text Extraction**: pdfplumber
* **PDF Generation**: reportlab
* **Image Processing**: Pillow (for receipt rendering)
* **Frontend**: HTML5, Vanilla CSS, Bootstrap 5, Bootstrap Icons

---

## 📁 Project Structure

```text
Shared_Expenses_App/
│
├── Shared_Expenses_App/             # Django settings & base configuration
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
│
├── splitly/                         # Core Application App
│   ├── migrations/                  # Database schema versions
│   ├── templates/splitly/           # HTML templates (Dashboards, Ingestion, Reports)
│   ├── admin.py                     # Custom admin interface registrations
│   ├── balance_engine.py            # Net balance and debt simplifiers
│   ├── csv_engine.py                # CSV statement parser & validators
│   ├── pdf_engine.py                # PDF statement parser & heuristic AI classifiers
│   ├── split_engine.py              # Split calculations & auto-suggestion logic
│   ├── reports.py                   # ReportLab PDF report generation builders
│   ├── models.py                    # Database schema definitions
│   ├── views.py                     # App controllers & views
│   ├── urls.py                      # App routes & endpoints
│   └── tests.py                     # Automated unit test suite
│
├── static/                          # Global CSS, JS, and image assets
├── db.sqlite3                       # Local database file
├── requirements.txt                 # Project library dependencies
└── manage.py                        # Django execution script
```

---

## ⚙️ Setup & Installation

### 1. Clone & Enter the Repository
```bash
cd Shared_Expenses_App
```

### 2. Create and Activate a Virtual Environment
```bash
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On macOS/Linux:
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run Migrations & Setup Database
```bash
python manage.py makemigrations
python manage.py migrate
```

### 5. Create a Superuser
```bash
python manage.py createsuperuser
```

### 6. Run the Development Server
```bash
python manage.py runserver
```
Visit the application at: `http://127.0.0.1:8000/`

---

## 🧪 Running Unit Tests
A clean unit test suite is provided to verify PDF text extraction, AI category mapping, duplicate detection matching, and history-based split suggestions:

```bash
python manage.py test
```
