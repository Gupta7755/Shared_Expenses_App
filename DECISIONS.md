# Splitly — Design Decision Log (DECISIONS.md)

This log tracks the major architectural, algorithmic, and technology design decisions made during the development of Splitly.

---

## 1. Local AI-Lite PDF Parsing & Extraction (No API Keys)
* **Problem**: Extracting structured financial transaction rows from unstructured text in bank statement/invoice PDFs.
* **Options Considered**:
  1. *Option A (Cloud LLM)*: Integrate Gemini or OpenAI GPT-4o APIs to extract JSON structured lists.
  2. *Option B (Cloud OCR)*: Use Google Cloud Vision API / Tesseract OCR to read visual coordinates.
  3. *Option C (Local pure-Python heuristic parser)*: Use `pdfplumber` to pull text offline, combined with regular expressions and classification keyword rules.
* **Choice**: **Option C (Local pure-Python heuristic parser)**.
* **Rationale**: 
  - Keeps the codebase 100% self-contained, lightweight, free, and fully functional offline without needing API keys or complex setups.
  - Guarantees fast execution times and matches expected text structures without non-deterministic LLM hallucinations or billing dependencies.

---

## 2. Advanced Heuristic-Based Duplicate Detection
* **Problem**: Preventing duplicate transaction entries during bulk CSV and PDF imports.
* **Options Considered**:
  1. *Option A (Exact match check)*: Require identical title string, amount, and date.
  2. *Option B (Smart similarity window)*: Match on title overlap, amount margin, and date range proximity.
* **Choice**: **Option B (Smart similarity window)**.
* **Rationale**: 
  - Real bank statements and receipts often format the same transaction with slight string variations (e.g. "Starbucks Coffee" vs "STARBUCKS - DELHI").
  - Checking for `amount ± 1`, `date ± 3 days`, and a `title word overlap ≥ 50%` provides high-precision matching that catches actual duplicates while avoiding false positives.

---

## 3. Base Currency Aggregation (Convert-to-INR)
* **Problem**: Simplification of debts containing a mix of international currencies (INR, USD, EUR, GBP, AED).
* **Options Considered**:
  1. *Option A (Isolate currencies)*: Keep separate balance calculations for each currency.
  2. *Option B (Standardized base conversion)*: Convert all transaction amounts to a single unified base currency (INR) for simplify-debt processing.
* **Choice**: **Option B (Standardized base conversion)**.
* **Rationale**: 
  - Running separate calculations for multiple currencies forces users to make several distinct payments to settle (e.g. Alice pays Bob $10, and Bob pays Alice ₹800).
  - Normalizing to INR at the time of entry allows running a single debt simplification pass, meaning users settle up in a single transaction in their preferred currency.

---

## 4. Cycle-Based Group Segmentation
* **Problem**: Settling balances in long-running groups.
* **Options Considered**:
  1. *Option A (Lifespan calculations)*: Compute debt simplification across the entire history of the group.
  2. *Option B (Group cycles)*: Split expenses into distinct "cycles" which must be closed and settled before starting a new cycle.
* **Choice**: **Option B (Group cycles)**.
* **Rationale**: 
  - Over the lifetime of a group (e.g. roommates over 2 years), balances shift and people pay back. Calculating all history creates confusion.
  - Cycle segmentation locks previous history, simplifies tracking, and prevents new expenses from affecting balances that have already been settled.

---

## 5. Rich, Custom Django Admin Views
* **Problem**: Auditability and admin access control during evaluation.
* **Options Considered**:
  1. *Option A (Default Django registration)*: Use standard `admin.site.register(Model)` registrations.
  2. *Option B (Customized ModelAdmin classes)*: Write tailored subclasses specifying listing columns, search indexes, and sidebar filter buckets.
* **Choice**: **Option B (Customized ModelAdmin classes)**.
* **Rationale**: 
  - Administrators need to audit audit logs, import histories, and verify anomaly rates rapidly.
  - Custom sidebar widgets and column lookups turn the default Django admin page into a premium-feeling management console.
