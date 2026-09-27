# Project brief

## Problem

Supplier invoices often arrive as PDFs while operational reporting happens in
spreadsheets. Re-entering invoice details requires repetitive work, and duplicate
or incomplete records can distort spending summaries. Analysts also need to
trace a reported amount back to its source document.

## Solution

Invoice Analytics Automation reads a folder of supported invoices, validates
five fields, identifies potential duplicates, and produces spreadsheet exports
and an offline spending dashboard. Files that fail validation are separated
into a review queue with an explanation and source filename.

A local review workspace lets an analyst inspect each source PDF, correct
extracted fields, and approve or reject records with notes. The reviewed export
contains approved records only, along with rejection details and decision history.
Progress is saved locally, and each completed export is retained as a separate
snapshot.

The intended users are small operations teams or analysts who repeatedly receive
invoices in known formats. This is a local portfolio implementation with
fictional demonstration data; it has not been deployed to a real client.

## Demonstrated results

The supplied batch has 18 PDFs: 12 valid invoices, one duplicate, and five files
with document or field problems. The valid invoices span two layouts, five
fictional suppliers, July through September 2026, and two currencies.

| Measure | Demonstration result |
| --- | --- |
| PDFs processed | 18 |
| Invoices accepted | 12 |
| Files requiring review | 6 |
| Accepted SGD total | 4,196.25 |
| Accepted USD total | 1,175.25 |

The 6/18 review proportion is deliberately constructed to exercise failure
handling. It is not an estimate of a business's invoice quality or the system's
real-world success rate.

The separate evaluation set contains eight additional synthetic PDFs authored
outside the demo generator. The recorded evaluation correctly extracts four
supported invoices (20 of 20 business fields match) and rejects four documents
for the expected reasons. These results describe that fixed set, not general
accuracy on supplier documents. See [the evaluation report](../examples/evaluation.json)
and [fixture provenance](../data/evaluation/README.md).

## Example analytical finding

Cloudline Services accounts for SGD 2,250.00, approximately 53.6% of accepted
SGD spending in the demonstration data. This could prompt an analyst to review
the underlying service contract or recurring charges. It does not demonstrate
overspending or savings on its own.

The September SGD total is lower than August's, but the dataset is small and
does not establish complete monthly coverage. A business recommendation would
require confirming completeness and considering the reporting period.

## Concise project explanation

> This project automates the step between receiving supplier invoices and
> producing a spending report. It extracts fields from two known PDF layouts,
> validates the values, flags duplicates and incomplete documents, and exports
> clean records to CSV and Excel. An offline dashboard summarizes spending by
> supplier and month while keeping currencies separate. The main design focus
> is traceability and visible exceptions so that questionable records do not
> silently enter the analysis.

## Decisions worth explaining

- Known layouts make the supported behavior testable and explicit.
- A review queue makes failures visible instead of silently dropping files.
- Explicit review decisions and correction notes preserve the connection between
  extracted values, human corrections, and the records used in a report.
- Conflicting versions of the same invoice are all excluded from spending until
  resolved, so filename order cannot decide which amount is reported.
- Source filenames let an analyst trace a result back to its document.
- Decimal arithmetic avoids binary floating-point rounding in aggregation.
- Currency separation avoids a meaningless combined total without exchange rates.
- Automated tests compare extracted fields with expected values and cover errors.
- A separately authored PDF evaluation set checks extraction and rejection
  outcomes beyond the original demo generator. Its score includes expected
  fields from incorrectly rejected documents, keeping failures visible.

The implementation uses Python, pypdf, openpyxl, Flask, and standard-library modules.
It does not use a language model to interpret invoices. AI-assisted development
and AI-powered invoice extraction are different claims; describe development
assistance accurately when discussing how the repository was built.

No measured time savings, real client adoption, or production-scale throughput
are claimed. A future business evaluation should compare the same document set
against manual processing and independently reviewed expected results.
