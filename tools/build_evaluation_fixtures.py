"""Build separately authored synthetic PDFs without the demo generator or CSV.

The literal document text below is intentionally separate from expected.json.
This uses pypdf content streams; the demonstration batch uses ReportLab.
Run only when deliberately replacing the committed evaluation PDFs.
"""

from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "data/evaluation/pdfs"

# Document text is authored here, not assembled from parser labels or expected
# values. Keep the separately recorded expectations out of this script.
DOCUMENTS = {
    "standard_reordered.pdf": [
        ["SYNTHETIC EVALUATION - NOT A PAYABLE INVOICE",
         "Total: 1,234.56", "Currency: eur", "Supplier: Seabrook Paperworks",
         "Invoice Date: 2026-06-30", "Invoice Number: SP-406",
         "Description: Paper supplies for the quarterly planning event"],
    ],
    "vendor_spacing.pdf": [
        ["SYNTHETIC EVALUATION - NOT A PAYABLE INVOICE",
         "  invoice id   :   FT-207  ", "  vendor : Fern   Training Studio",
         "issued  :  2026-07-14", "currency : gbp", "amount due : 78.5",
         "Description: Staff workshop materials"],
    ],
    "standard_two_pages.pdf": [
        ["SYNTHETIC EVALUATION - PAGE 1 OF 2",
         "Invoice Number: OC-118", "Supplier: Orchard Computing",
         "Invoice Date: 2026-08-03", "Description: Office workstation rental"],
        ["SYNTHETIC EVALUATION - PAGE 2 OF 2",
         "Currency: AUD", "Total: 2400", "Payment terms: Thirty days"],
    ],
    "vendor_case_two_pages.pdf": [
        ["SYNTHETIC EVALUATION - PAGE 1 OF 2",
         "vEnDoR: Copper Kite Logistics", "aMoUnT dUe: 900.07",
         "iNvOiCe Id: CK-512", "Description: Equipment delivery"],
        ["SYNTHETIC EVALUATION - PAGE 2 OF 2",
         "cUrReNcY: CAD", "iSsUeD: 2026-08-22", "Reference: PO-607"],
    ],
    "unsupported_labels.pdf": [
        ["SYNTHETIC EVALUATION - NOT A PAYABLE INVOICE",
         "Bill No: U-310", "Payee: Juniper Displays", "Dated: 2026-07-02",
         "Currency: SGD", "Balance: 110.00"],
    ],
    "repeated_total_two_pages.pdf": [
        ["SYNTHETIC EVALUATION - PAGE 1 OF 2",
         "Invoice Number: RP-220", "Supplier: Lantern Office Services",
         "Invoice Date: 2026-07-19", "Currency: USD", "Total: 450.00"],
        ["SYNTHETIC EVALUATION - PAGE 2 OF 2",
         "Total: 475.00", "Description: Conflicting printed total"],
    ],
    "missing_amount.pdf": [
        ["SYNTHETIC EVALUATION - NOT A PAYABLE INVOICE",
         "Invoice ID: MS-082", "Vendor: Saffron Workshop",
         "Issued: 2026-08-18", "Currency: SGD", "Description: Total omitted"],
    ],
    "invalid_calendar_date.pdf": [
        ["SYNTHETIC EVALUATION - NOT A PAYABLE INVOICE",
         "Invoice Number: DT-229", "Supplier: Pebble Design Studio",
         "Invoice Date: 2026-02-29", "Currency: USD", "Total: 65.25"],
    ],
}


def build_fixtures(destination: Path = DESTINATION) -> None:
    """Write selectable text on US Letter pages using raw PDF operators."""
    destination.mkdir(parents=True, exist_ok=True)
    for filename, pages in DOCUMENTS.items():
        writer = PdfWriter()
        writer.add_metadata({"/Title": "Synthetic invoice evaluation",
                             "/Author": "AI-assisted portfolio project"})
        for lines in pages:
            page = writer.add_blank_page(width=612, height=792)
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Courier"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            })
            operators = ["BT", "/F1 10 Tf", "18 TL", "48 738 Td"]
            for line in lines:
                escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                operators.extend([f"({escaped}) Tj", "T*"])
            operators.append("ET")
            content = DecodedStreamObject()
            content.set_data("\n".join(operators).encode("ascii"))
            page.replace_contents(content)
        writer.write(destination / filename)
    print(f"Wrote {len(DOCUMENTS)} synthetic evaluation PDFs to {destination}")


if __name__ == "__main__":
    build_fixtures()
