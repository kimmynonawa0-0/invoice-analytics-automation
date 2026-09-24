"""Extract text and validate the two explicitly supported invoice layouts."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .models import Invoice, InvoiceError


LAYOUTS = {
    "standard": {
        "invoice_number": "Invoice Number",
        "supplier": "Supplier",
        "invoice_date": "Invoice Date",
        "currency": "Currency",
        "total": "Total",
    },
    "vendor": {
        "invoice_number": "Invoice ID",
        "supplier": "Vendor",
        "invoice_date": "Issued",
        "currency": "Currency",
        "total": "Amount Due",
    },
}
SUPPORTED_CURRENCIES = {"SGD", "USD", "EUR", "GBP", "AUD", "CAD"}
AMOUNT_PATTERN = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,2})?")


def read_pdf_text(path: Path) -> str:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise InvoiceError("encrypted_pdf", "Password-protected PDFs are not supported.")
        pages = [page.extract_text() or "" for page in reader.pages]
    except InvoiceError:
        raise
    except (OSError, PyPdfError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise InvoiceError("unreadable_pdf", "The PDF could not be read. Check the source file.") from exc

    # Reject partially image-only documents as well as fully blank documents.
    if not pages or any(not text.strip() for text in pages):
        raise InvoiceError("no_text", "At least one page has no extractable text; manual review or OCR is required.")
    return "\n".join(pages)


def parse_invoice(text: str, source_file: str) -> Invoice:
    text = "\n".join(line.strip() for line in text.splitlines())
    candidates = [
        name for name, labels in LAYOUTS.items()
        if re.search(rf"^{re.escape(labels['invoice_number'])}\s*:", text, re.MULTILINE | re.IGNORECASE)
    ]
    if len(candidates) != 1:
        raise InvoiceError("unsupported_layout", "Expected exactly one supported invoice layout per PDF.")

    layout = candidates[0]
    values = {}
    for field, label in LAYOUTS[layout].items():
        matches = re.findall(rf"^{re.escape(label)}[ \t]*:[ \t]*([^\r\n]*)$", text, re.MULTILINE | re.IGNORECASE)
        if len(matches) > 1:
            raise InvoiceError("ambiguous_field", f"Repeated field: {label}. Review the document manually.")
        if not matches or not matches[0].strip():
            raise InvoiceError("missing_field", f"Missing required field: {label}.")
        values[field] = " ".join(matches[0].split())

    raw_date = values["invoice_date"]
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date):
            raise ValueError
        invoice_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise InvoiceError("invalid_date", "Invoice date must be a valid YYYY-MM-DD date.") from exc

    currency = values["currency"].upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise InvoiceError("unsupported_currency", "Supported currencies: " + ", ".join(sorted(SUPPORTED_CURRENCIES)) + ".")

    raw_total = values["total"]
    if not AMOUNT_PATTERN.fullmatch(raw_total) or len(raw_total) > 20:
        raise InvoiceError("invalid_total", "Total must be positive, with at most two decimal places; comma groups must contain three digits.")
    total = Decimal(raw_total.replace(",", ""))
    if total <= 0 or total > Decimal("999999999999.99"):
        raise InvoiceError("invalid_total", "Total must be greater than zero and at most 999999999999.99.")

    return Invoice(source_file, layout, values["invoice_number"], values["supplier"], invoice_date, currency, total.quantize(Decimal("0.01")))
