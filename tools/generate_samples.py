"""Generate the fictional demonstration batch, including review cases."""

import csv
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]


def render_pdf(path: Path, lines: list[str], vendor_layout: bool = False) -> None:
    document = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    document.setTitle("Fictional supplier invoice")
    document.setAuthor("Invoice Analytics Automation")
    document.setFillColor(colors.HexColor("#087F80" if vendor_layout else "#153B4B"))
    document.rect(0, 700, A4[0], 142, fill=1, stroke=0)
    document.setFillColor(colors.white)
    document.setFont("Helvetica-Bold", 26)
    document.drawString(48, 776, "SUPPLIER INVOICE" if vendor_layout else "INVOICE")
    document.setFont("Helvetica", 10)
    document.drawString(48, 742, "FICTIONAL DEMONSTRATION DATA")
    document.setFillColor(colors.HexColor("#153B4B"))
    text = document.beginText(48, 658)
    text.setFont("Helvetica", 12)
    text.setLeading(30)
    for line in lines:
        text.textLine(line)
    document.drawText(text)
    document.setFont("Helvetica", 9)
    document.drawString(48, 48, "All supplier names and transactions are fictional. Not a payable invoice.")
    document.showPage()
    document.save()


def invoice_lines(row: dict[str, str]) -> list[str]:
    if row["layout"] == "vendor":
        return [f"Vendor: {row['supplier']}", f"Invoice ID: {row['invoice_number']}",
                f"Issued: {row['invoice_date']}", f"Currency: {row['currency']}",
                "Description: Business services and supplies", f"Amount Due: {row['total']}"]
    return [f"Invoice Number: {row['invoice_number']}", f"Supplier: {row['supplier']}",
            f"Invoice Date: {row['invoice_date']}", f"Currency: {row['currency']}",
            "Description: Business services and supplies", f"Total: {row['total']}"]


def generate_samples(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with (ROOT / "data/expected_invoices.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        render_pdf(destination / row["source_file"], invoice_lines(row), row["layout"] == "vendor")
    first = rows[0]
    render_pdf(destination / "z_duplicate.pdf", invoice_lines(first))
    missing = dict(first, invoice_number="INV-MISSING")
    render_pdf(destination / "z_missing_total.pdf", [line for line in invoice_lines(missing) if not line.startswith("Total:")])
    render_pdf(destination / "z_invalid_date.pdf", invoice_lines(dict(first, invoice_number="INV-DATE", invoice_date="2026-02-30")))
    render_pdf(destination / "z_invalid_total.pdf", invoice_lines(dict(first, invoice_number="INV-TOTAL", total="-100.00")))
    blank = canvas.Canvas(str(destination / "z_blank.pdf"), pagesize=A4, invariant=1)
    blank.showPage()
    blank.save()
    (destination / "z_corrupt.pdf").write_bytes(b"%PDF-1.4\nIntentionally truncated demonstration file.\n")
    print(f"Generated {len(rows)} valid invoices and 6 review cases in {destination}")


if __name__ == "__main__":
    generate_samples(ROOT / "data/samples")
