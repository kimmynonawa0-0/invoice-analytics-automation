"""Batch processing with deterministic ordering and duplicate review."""

from pathlib import Path

from .extraction import parse_invoice, read_pdf_text
from .models import BatchResult, Invoice, InvoiceError, Issue


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
    parsed: list[Invoice] = []
    groups: dict[tuple[str, str], list[Invoice]] = {}
    for path in paths:
        try:
            invoice = parse_invoice(read_pdf_text(path), path.name)
        except InvoiceError as exc:
            result.issues.append(Issue(path.name, exc.code, str(exc)))
            continue

        parsed.append(invoice)
        groups.setdefault(invoice.duplicate_key, []).append(invoice)

    # Check whole groups before accepting any member. A later conflict must also
    # exclude earlier copies, including copies whose values match each other.
    conflicts = {
        key: group
        for key, group in groups.items()
        if len({(item.invoice_date, item.currency, item.total) for item in group}) > 1
    }
    seen: dict[tuple[str, str], Invoice] = {}
    for invoice in parsed:
        conflicting_group = conflicts.get(invoice.duplicate_key)
        if conflicting_group is not None:
            files = ", ".join(item.source_file for item in conflicting_group)
            result.issues.append(Issue(
                invoice.source_file,
                "conflicting_duplicate",
                f"Supplier and invoice number have conflicting dates, currencies, or totals across {files}; "
                "all files in this group are excluded. Verify the correct record.",
            ))
            continue

        previous = seen.get(invoice.duplicate_key)
        if previous is not None:
            result.issues.append(Issue(invoice.source_file, "duplicate_invoice", f"Supplier and invoice number already seen in {previous.source_file}; this file is excluded. Verify both documents."))
            continue
        seen[invoice.duplicate_key] = invoice
        result.invoices.append(invoice)

    result.issues.sort(key=lambda issue: issue.source_file)
    return result
