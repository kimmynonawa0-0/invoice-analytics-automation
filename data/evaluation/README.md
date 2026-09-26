# Synthetic extraction evaluation

This directory contains eight fictional PDFs and a separate expected-results
manifest. They add checks beyond the demonstration PDFs in `data/samples`.
Four documents should be accepted; four should be rejected with specific reasons.

## Provenance and limits

The PDFs were authored with AI assistance as literal document text in
`tools/build_evaluation_fixtures.py`. That script writes PDF content streams using
pypdf. It does not import the extraction labels or the ReportLab demonstration
generator, and does not read either expected-results file. Expected values were
transcribed separately into `expected.json`, also with AI assistance.

These are synthetic fixtures created through a separate path, not independently
human-validated invoices or documents collected from real suppliers. Both PDF
creation and extraction use pypdf, and the cases were selected with knowledge of
the supported labels. Passing this set demonstrates these eight examples only;
it does not estimate accuracy on arbitrary invoices, scans, tables, fonts, or
other software's PDF exports. Broader evaluation would require independently
obtained documents and human-verified expected values.

All supplier names and transactions are fictional. These are not payable invoices.

## Cases

| PDF | Expected outcome | Behavior exercised |
| --- | --- | --- |
| `standard_reordered.pdf` | Accepted | Reordered standard fields, lowercase currency, thousands separator |
| `vendor_spacing.pdf` | Accepted | Lowercase vendor labels, spaces around colons, repeated supplier spaces, one decimal place |
| `standard_two_pages.pdf` | Accepted | Required fields split across two pages, whole-number total |
| `vendor_case_two_pages.pdf` | Accepted | Mixed-case vendor labels and required fields split across two pages |
| `unsupported_labels.pdf` | `unsupported_layout` | Text present, but required label sets absent |
| `repeated_total_two_pages.pdf` | `ambiguous_field` | Conflicting totals printed on different pages |
| `missing_amount.pdf` | `missing_field` | Vendor layout missing its amount due |
| `invalid_calendar_date.pdf` | `invalid_date` | February 29 in a year without that date |

## Run the evaluation

From the repository root with its environment activated:

```powershell
python -m tools.evaluate --output outputs/evaluation.json
```

The command reads the committed PDFs without regenerating them. It prints a
summary and per-document outcomes; the optional JSON contains expected and actual
records, field comparisons, rejection codes, and integrity errors. Use
`--fixtures` and `--expected` together to evaluate another directory and manifest
with the same schema.

Exit codes are `0` when everything matches, `1` when results differ, and `2` when
inputs are invalid or the evaluation cannot run. Missing or extra PDFs,
incomplete manifests, unaccounted-for results, and an incorrect processed count
cannot produce a passing evaluation.

`matched_fields` and `expected_fields` cover the five business fields: invoice
number, supplier, invoice date, currency, and total. The denominator is **20** for
the four expected valid documents, even if the pipeline rejects one incorrectly.
Source filename and layout must also match for a document to count as a correct
extraction. Expected rejection cases contribute to rejection counts, not the
field denominator. A rejected document counts as correct only if its rejection
code matches. Incorrect extraction, incorrect acceptance, incorrect rejection,
and wrong rejection reason have separate counters.

## Maintaining the fixtures

The committed PDFs are the test inputs. Neither tests nor CI rebuild them. To
deliberately replace the PDFs after editing their separately authored text:

```powershell
python -m tools.build_evaluation_fixtures
```

Review each changed PDF and its expected record separately before accepting a
new baseline. Keep real client data out of this public evaluation set. The builder
and evaluator use the existing runtime dependencies; no additional package is
required.
