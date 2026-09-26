# Invoice Analytics Automation

Turn a folder of supplier invoices into validated spreadsheet records and a
spending report. The application extracts fields from supported PDF layouts,
flags documents that need review, and summarizes accepted invoices by currency,
month, and supplier.

![Invoice spending dashboard](docs/assets/dashboard.png)

## AI-assisted development

This portfolio project was developed with substantial AI assistance, including
the implementation, automated tests, documentation, and demonstration assets.
The project direction and scope were selected by the author, with AI used to
help build the working application.

The initial version is prepared for publication as a single baseline commit.
That commit captures the completed initial implementation; the repository's
history does not document the intermediate AI-assisted development steps.

Invoice processing itself uses explicit parsing and validation rules. Running
the application does not call an AI model or require an AI subscription.

## Business problem

Operations teams often receive invoices as PDFs but track spending in Excel.
Copying invoice details manually is repetitive, while missing values and
duplicates can distort reports. This project automates extraction and reporting
and keeps a review queue for documents that do not meet its validation rules.

**Implemented scope:** batch processing for two documented, selectable-text
invoice layouts. The included demonstration uses fictional data. See the
[project brief](docs/PROJECT_BRIEF.md) for the business context, findings, and
limitations, and the [architecture](docs/ARCHITECTURE.md) for design decisions.

## Features

- Extract invoice number, supplier, date, currency, and total from two layouts.
- Validate required fields, dates, currency codes, and positive decimal amounts.
- Detect duplicate supplier/invoice-number pairs and conflicting values.
- Continue processing other files when a PDF is unreadable or fails validation.
- Export accepted records and review reasons, with source filenames for traceability.
- Produce a formatted Excel workbook and an offline HTML dashboard with a currency filter.
- Calculate spending by supplier and month without combining currencies.
- Rebuild reports on every run, with command exit statuses suitable for scheduled jobs.

## Quick start

Requires Python 3.10 or newer. Run commands from the repository root.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m invoice_analytics --input data/samples --output outputs
Start-Process .\outputs\dashboard.html
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m invoice_analytics --input data/samples --output outputs
```

Open `outputs/dashboard.html` in a browser. No web server, account, API key, or
network connection is required after installation. The sample batch intentionally
contains a corrupt PDF; a PDF-library warning during this demo is expected.

Alternatively, install the package with `python -m pip install .` inside your
environment, then use `invoice-analytics --input data/samples --output outputs`.

## Demo walkthrough

After completing setup, use this sequence from the repository root. The same
virtual environment can be reused on subsequent runs.

1. Open `data/samples/invoice_001.pdf` in a PDF viewer. Identify its invoice
   number (`INV-001`), supplier (`Harbor Office Supplies`), date (`2026-09-01`),
   currency (`SGD`), and total (`125.50`). These are the fields the tool extracts.
2. Run the batch in PowerShell:

   ```powershell
   .\.venv\Scripts\python.exe -m invoice_analytics --input data/samples --output outputs
   ```

   `--input` selects the folder of PDFs. `--output` selects where reports are
   saved. Expect 18 processed files, 12 accepted invoices, and 6 review files.
3. Open the dashboard:

   ```powershell
   Start-Process .\outputs\dashboard.html
   ```

   Inspect the processing counts, select SGD or USD using the currency filter,
   and review spending by month and supplier. Scroll to the review queue to see
   why files were excluded. Currency selection filters the spending sections;
   the processing counts and review queue always describe the entire batch.
4. Open `outputs/invoices.csv` and find `invoice_001.pdf`. Compare its row with
   the source PDF. Then open `outputs/review.csv`: `z_duplicate.pdf` should be
   flagged as a duplicate, and `z_missing_total.pdf` should have a missing-field
   reason. The six review cases are intentional demonstration inputs.
5. Open `outputs/spend_report.xlsx` in Excel or another compatible spreadsheet
   application. Its five sheets contain accepted invoices, review details,
   currency totals, monthly spending, and supplier spending. `summary.json`
   contains the aggregate data in a format other programs can read.

The dashboard also links to the CSV files and Excel workbook. To process a
different batch, change `--input` to its folder and rerun the command. Close
the workbook first, then refresh the browser to see the regenerated report.

## Demonstration results

| Result | Value |
| --- | --- |
| PDF files processed | 18 |
| Accepted invoices | 12 |
| Files requiring review | 6 |
| Accepted SGD spending | 4,196.25 |
| Accepted USD spending | 1,175.25 |

The review cases cover a duplicate, a missing total, an invalid date, a negative
total, a blank PDF, and a corrupt PDF. The reference records are committed in
[`data/expected_invoices.csv`](data/expected_invoices.csv) and
[`data/expected_review.csv`](data/expected_review.csv).

Browse the [sample report files](examples/report), including the
[invoice CSV](examples/report/invoices.csv), [review CSV](examples/report/review.csv),
and [summary JSON](examples/report/summary.json). Download the sample report
folder to open its HTML dashboard locally; GitHub's file view does not run it.

## Outputs

| File | Contents |
| --- | --- |
| `invoices.csv` | Validated invoice records, source filenames, and detected layouts |
| `review.csv` | Excluded files, reason codes, and review details |
| `spend_report.xlsx` | Invoices, review queue, currency totals, monthly and supplier summaries |
| `dashboard.html` | Offline report with charts, supplier tables, and review details |
| `summary.json` | Counts and currency-specific aggregates for downstream use |

Every run replaces these five files in the selected output folder. It does not
append invoices or maintain a history. Close the generated workbook before
rerunning. Keep the five files together so dashboard download links work.
CSV and JSON amounts retain two decimal places. CSV text that resembles a
formula is prefixed with an apostrophe for spreadsheet safety.

## Process your own files

```powershell
New-Item -ItemType Directory -Force data/private | Out-Null
# Put your supported invoice PDFs in data/private before running this command.
.\.venv\Scripts\python.exe -m invoice_analytics --input data/private --output outputs
```

Create `data/private/` and put your PDFs there. It is excluded from Git, as are
the generated `outputs/` and virtual environment. PDF discovery is limited to
the immediate input folder, with case-insensitive `.pdf` extensions. Paths are
relative to the current terminal directory; absolute paths also work.

Each PDF must contain one invoice with one of these label sets. Each field must
be on its own extracted-text line in `Label: value` form.

| Field | Standard layout | Vendor layout |
| --- | --- | --- |
| Invoice number | `Invoice Number:` | `Invoice ID:` |
| Supplier | `Supplier:` | `Vendor:` |
| Date | `Invoice Date:` | `Issued:` |
| Currency | `Currency:` | `Currency:` |
| Total | `Total:` | `Amount Due:` |

Dates must use `YYYY-MM-DD`. Supported currencies are SGD, USD, EUR, GBP, AUD,
and CAD. Totals must be positive with at most two decimal places; `1,234.50`
is accepted. Repeated labels, missing fields, invalid values, unsupported
layouts, encrypted PDFs, and pages without text require review. No OCR is used.

Duplicate detection compares supplier and invoice number without case
sensitivity. When all matching records agree on date, currency, and total,
the first file in filename order is retained and later copies are excluded.
If any matching record disagrees on those values, **every invoice in that group
is excluded from spending totals** and marked `conflicting_duplicate` in the
review report. The review details identify the related source files.

For example, two invoices for the same supplier and invoice number showing
SGD 125.50 and SGD 999.00 both require review. Neither amount is accepted just
because its file was processed first. This also applies if a third file is an
exact copy of one of those conflicting invoices.

To resolve a conflict, verify the documents and create a corrected input batch
containing only the authoritative invoice for that identity, then rerun the
command. Retain the original documents separately for reference. There is no
deduplication across runs.

### Exit statuses

| Status | Meaning |
| --- | --- |
| `0` | Reports generated; inspect the review count for excluded files |
| `1` | Input or report-writing failure |
| `2` | Invalid command arguments, or review files found with `--fail-on-review` |

Use `--fail-on-review` when a scheduler should flag an incomplete batch.
Reports are still generated when that flag returns status 2. If every file is
rejected, the dashboard shows an empty-data message and the complete review queue.

## Project structure

```text
invoice_analytics/       Extraction, validation, batch processing, reports, CLI
data/samples/           Fictional PDFs, including intentional review cases
data/expected_*.csv      Reference values used by tests
examples/report/        Committed sample outputs for portfolio review
docs/                   Project brief, architecture, and dashboard preview
tests/                  Automated behavior and report checks
tools/generate_samples.py
.github/workflows/tests.yml
pyproject.toml
requirements.txt
requirements-dev.txt
```

## Development and verification

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests cover expected extraction values, both layouts, invalid inputs, duplicate
conflicts, encrypted files, currency totals, exports, reruns, escaping, and exit
statuses. GitHub Actions is configured to run the suite on Python 3.10 and 3.14.

The fictional PDFs are included. To regenerate them and refresh the committed
demonstration reports after changing the fixtures:

```powershell
.\.venv\Scripts\python.exe tools/generate_samples.py
.\.venv\Scripts\python.exe -m invoice_analytics --input data/samples --output examples/report
```

## Scope and limitations

The supplied fixtures demonstrate supported behavior; they do not establish
accuracy on arbitrary supplier documents. Scanned invoices, handwriting, line
items, tax reconciliation, payment matching, exchange-rate conversion, and
supplier alias matching are outside the current scope. The tool validates field
structure but cannot verify that an invoice is authentic or that its amount is
correct against a purchase order.

Spending is based on invoice dates and accepted amounts, not confirmed payments.
Missing months are omitted from charts. The demo is a small synthetic dataset,
so its findings are illustrative rather than evidence about an actual business.
