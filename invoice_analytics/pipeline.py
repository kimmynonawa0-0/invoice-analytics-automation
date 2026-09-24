"""Batch processing with deterministic ordering and duplicate review."""

from pathlib import Path

from .extraction import parse_invoice, read_pdf_text
from .models import BatchResult, InvoiceError, Issue


def process_directory(input_dir: Path) -> BatchResult:
    if not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    paths = sorted(
        (path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.name,
    )
    if not paths:
        raise ValueError(f"No PDF files found in: {input_dir}")

    result = BatchResult(invoices=[], issues=[], processed=len(paths))
    seen = {}
    for path in paths:
        try:
            invoice = parse_invoice(read_pdf_text(path), path.name)
        except InvoiceError as exc:
            result.issues.append(Issue(path.name, exc.code, str(exc)))
            continue

        previous = seen.get(invoice.duplicate_key)
        if previous is not None:
            same_values = (invoice.invoice_date, invoice.currency, invoice.total) == (previous.invoice_date, previous.currency, previous.total)
            code = "duplicate_invoice" if same_values else "conflicting_duplicate"
            result.issues.append(Issue(path.name, code, f"Supplier and invoice number already seen in {previous.source_file}; this file is excluded. Verify both documents."))
            continue
        seen[invoice.duplicate_key] = invoice
        result.invoices.append(invoice)

    return result
