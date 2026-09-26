"""Compare committed evaluation PDFs with separately recorded expectations."""

import argparse
import json
import sys
from pathlib import Path

from invoice_analytics.models import BatchResult
from invoice_analytics.pipeline import process_directory


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data/evaluation/pdfs"
EXPECTED = ROOT / "data/evaluation/expected.json"
BUSINESS_FIELDS = ("invoice_number", "supplier", "invoice_date", "currency", "total")
INVOICE_FIELDS = {"layout", *BUSINESS_FIELDS}


def load_expectations(expected_path: Path, fixture_dir: Path) -> list[dict]:
    """Reject incomplete manifests and mismatched PDF inventories before scoring."""
    document = json.loads(expected_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Expected a manifest with schema_version 1.")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Expected at least one evaluation case.")
    filenames = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Each evaluation case must be an object.")
        filename = case.get("source_file")
        if (not isinstance(filename, str) or not filename.lower().endswith(".pdf")
                or "/" in filename or "\\" in filename or ":" in filename):
            raise ValueError("Each source_file must be a PDF filename without a directory.")
        if filename.casefold() in filenames:
            raise ValueError(f"Duplicate expected filename: {filename}")
        filenames.add(filename.casefold())
        if case.get("expected_status") == "accepted":
            invoice = case.get("expected_invoice")
            if (not isinstance(invoice, dict) or set(invoice) != INVOICE_FIELDS
                    or any(not isinstance(value, str) or not value for value in invoice.values())):
                raise ValueError(f"Expected all six invoice fields as nonempty strings: {filename}")
        elif case.get("expected_status") == "rejected":
            code = case.get("expected_code")
            if not isinstance(code, str) or not code.strip():
                raise ValueError(f"Expected a rejection code: {filename}")
        else:
            raise ValueError(f"Unknown expected_status: {filename}")

    if not fixture_dir.is_dir():
        raise ValueError(f"Evaluation fixture directory does not exist: {fixture_dir}")
    expected_names = {case["source_file"] for case in cases}
    # Nested PDFs are included in this check, because the pipeline would ignore
    # them. They must not silently disappear from the evaluated inventory.
    actual_names = {path.relative_to(fixture_dir).as_posix() for path in fixture_dir.rglob("*")
                    if path.is_file() and path.suffix.lower() == ".pdf"}
    missing, extra = sorted(expected_names - actual_names), sorted(actual_names - expected_names)
    if missing or extra:
        raise ValueError(f"Evaluation PDF inventory mismatch. Missing: {missing}; unexpected: {extra}")
    return cases


def score_results(result: BatchResult, cases: list[dict]) -> dict:
    """Score fields on all expected valid documents, including false rejections."""
    metrics = {
        "expected_documents": len(cases),
        "expected_accepted": sum(case["expected_status"] == "accepted" for case in cases),
        "expected_rejected": sum(case["expected_status"] == "rejected" for case in cases),
        "correct_extraction": 0,
        "correct_rejection": 0,
        "incorrect_extraction": 0,
        "incorrect_acceptance": 0,
        "incorrect_rejection": 0,
        "wrong_rejection_reason": 0,
        "matched_fields": 0,
        "expected_fields": 0,
    }
    actual = {}
    for invoice in result.invoices:
        actual.setdefault(invoice.source_file, []).append({
            "status": "accepted", "invoice": invoice.as_row(),
        })
    for issue in result.issues:
        actual.setdefault(issue.source_file, []).append({
            "status": "rejected", "code": issue.code, "message": issue.message,
        })

    integrity_errors = []
    expected_names = {case["source_file"] for case in cases}
    if result.processed != len(cases):
        integrity_errors.append(f"Pipeline counted {result.processed} PDFs; expected {len(cases)}.")
    for filename in sorted(set(actual) - expected_names):
        integrity_errors.append(f"Unexpected pipeline result: {filename}")

    details = []
    for case in cases:
        filename = case["source_file"]
        records = actual.get(filename, [])
        detail = {"source_file": filename, "expected_status": case["expected_status"], "passed": False}
        if len(records) != 1:
            integrity_errors.append(f"Expected one pipeline result for {filename}; received {len(records)}.")
        record = records[0] if len(records) == 1 else {"status": "missing_or_multiple"}
        detail["actual_status"] = record["status"]
        if "invoice" in record:
            detail["actual_invoice"] = record["invoice"]
        if "code" in record:
            detail["actual_code"] = record["code"]
            detail["actual_message"] = record["message"]

        if case["expected_status"] == "accepted":
            expected_invoice = {"source_file": filename, **case["expected_invoice"]}
            actual_invoice = record.get("invoice", {})
            detail["expected_invoice"] = expected_invoice
            detail["field_matches"] = {
                field: actual_invoice.get(field) == expected_invoice[field] for field in BUSINESS_FIELDS
            }
            metrics["expected_fields"] += len(BUSINESS_FIELDS)
            metrics["matched_fields"] += sum(detail["field_matches"].values())
            if record["status"] == "accepted":
                detail["passed"] = actual_invoice == expected_invoice
                detail["mismatched_fields"] = [
                    field for field in expected_invoice if actual_invoice.get(field) != expected_invoice[field]
                ]
                metrics["correct_extraction" if detail["passed"] else "incorrect_extraction"] += 1
            elif record["status"] == "rejected":
                metrics["incorrect_rejection"] += 1
        else:
            detail["expected_code"] = case["expected_code"]
            if record["status"] == "rejected":
                detail["passed"] = record["code"] == case["expected_code"]
                metrics["correct_rejection" if detail["passed"] else "wrong_rejection_reason"] += 1
            elif record["status"] == "accepted":
                metrics["incorrect_acceptance"] += 1
        details.append(detail)

    return {
        "evaluation_set": "synthetic-independent-v1",
        "scope": "Separately authored synthetic fixtures; not a real-world accuracy estimate.",
        "passed": not integrity_errors and all(detail["passed"] for detail in details),
        "metrics": metrics,
        "integrity_errors": integrity_errors,
        "cases": details,
    }


def evaluate(fixture_dir: Path = FIXTURES, expected_path: Path = EXPECTED) -> dict:
    cases = load_expectations(expected_path, fixture_dir)
    return score_results(process_directory(fixture_dir), cases)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES, help="Directory containing evaluation PDFs")
    parser.add_argument("--expected", type=Path, default=EXPECTED, help="Expected outcomes JSON manifest")
    parser.add_argument("--output", type=Path, help="Also write the full evaluation result as JSON")
    args = parser.parse_args(argv)
    try:
        report = evaluate(args.fixtures, args.expected)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"Evaluation could not run: {exc}", file=sys.stderr)
        return 2

    print("Synthetic evaluation: " + ("PASS" if report["passed"] else "FAIL"))
    for key, value in report["metrics"].items():
        print(f"  {key}: {value}")
    for case in report["cases"]:
        label = "PASS" if case["passed"] else "FAIL"
        reason = case.get("actual_code", case["actual_status"])
        print(f"  {label} {case['source_file']}: {reason}")
    for error in report["integrity_errors"]:
        print(f"  INTEGRITY ERROR: {error}")
    print(report["scope"])
    if args.output:
        print(f"Detailed results: {args.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
