"""Shared records passed between extraction, validation, and reporting."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Invoice:
    source_file: str
    layout: str
    invoice_number: str
    supplier: str
    invoice_date: date
    currency: str
    total: Decimal

    @property
    def duplicate_key(self) -> tuple[str, str]:
        # Supplier invoice numbers are assumed unique across the input batch.
        return (self.supplier.casefold(), self.invoice_number.casefold())

    def as_row(self) -> dict[str, str]:
        return {
            "source_file": self.source_file,
            "layout": self.layout,
            "invoice_number": self.invoice_number,
            "supplier": self.supplier,
            "invoice_date": self.invoice_date.isoformat(),
            "currency": self.currency,
            "total": format(self.total, ".2f"),
        }


@dataclass(frozen=True)
class Issue:
    source_file: str
    code: str
    message: str


@dataclass
class BatchResult:
    invoices: list[Invoice]
    issues: list[Issue]
    processed: int


class InvoiceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
