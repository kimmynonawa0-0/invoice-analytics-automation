"""Evaluation contracts using committed PDFs and deliberately incorrect results."""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from invoice_analytics.models import BatchResult, Issue
from invoice_analytics.pipeline import process_directory
from tools.evaluate import EXPECTED, FIXTURES, ROOT, evaluate, load_expectations, main


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Read the checked-in PDFs. Tests must never recreate their inputs from
        # expectations or call either fixture generator.
        cls.batch = process_directory(FIXTURES)

    def score_batch(self, batch):
        with patch("tools.evaluate.process_directory", return_value=batch):
            return evaluate()

    def test_committed_pdfs_match_separate_expectations(self):
        report = evaluate()
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["metrics"], {
            "expected_documents": 8, "expected_accepted": 4, "expected_rejected": 4,
            "correct_extraction": 4, "correct_rejection": 4,
            "incorrect_extraction": 0, "incorrect_acceptance": 0,
            "incorrect_rejection": 0, "wrong_rejection_reason": 0,
            "matched_fields": 20, "expected_fields": 20,
        })
        self.assertEqual(len(report["cases"]), 8)
        self.assertEqual(report["integrity_errors"], [])

    def test_every_business_field_is_scored(self):
        invoice = self.batch.invoices[0]
        incorrect_values = {
            "invoice_number": "WRONG-999", "supplier": "Incorrect supplier",
            "invoice_date": date(2025, 1, 1), "currency": "SGD", "total": Decimal("0.01"),
        }
        for field, value in incorrect_values.items():
            with self.subTest(field=field):
                bad_invoice = replace(invoice, **{field: value})
                batch = BatchResult([bad_invoice, *self.batch.invoices[1:]], self.batch.issues, 8)
                report = self.score_batch(batch)
                self.assertFalse(report["passed"])
                self.assertEqual(report["metrics"]["incorrect_extraction"], 1)
                self.assertEqual(report["metrics"]["matched_fields"], 19)
                self.assertEqual(report["metrics"]["expected_fields"], 20)
                case = next(item for item in report["cases"] if item["source_file"] == invoice.source_file)
                self.assertEqual(case["mismatched_fields"], [field])

    def test_wrong_layout_fails_even_when_business_fields_match(self):
        invoice = replace(self.batch.invoices[0], layout="incorrect-layout")
        report = self.score_batch(BatchResult([invoice, *self.batch.invoices[1:]], self.batch.issues, 8))
        self.assertFalse(report["passed"])
        self.assertEqual(report["metrics"]["incorrect_extraction"], 1)
        self.assertEqual(report["metrics"]["matched_fields"], 20)

    def test_false_rejection_keeps_all_expected_fields_in_denominator(self):
        invoice = self.batch.invoices[0]
        issue = Issue(invoice.source_file, "unsupported_layout", "Simulated extraction regression")
        report = self.score_batch(BatchResult(self.batch.invoices[1:], [*self.batch.issues, issue], 8))
        self.assertFalse(report["passed"])
        self.assertEqual(report["metrics"]["incorrect_rejection"], 1)
        self.assertEqual(report["metrics"]["matched_fields"], 15)
        self.assertEqual(report["metrics"]["expected_fields"], 20)

    def test_false_acceptance_and_wrong_rejection_reason_are_distinct(self):
        issue = self.batch.issues[0]
        invoice = replace(self.batch.invoices[0], source_file=issue.source_file)
        accepted = self.score_batch(BatchResult([*self.batch.invoices, invoice], self.batch.issues[1:], 8))
        self.assertFalse(accepted["passed"])
        self.assertEqual(accepted["metrics"]["incorrect_acceptance"], 1)
        self.assertEqual(accepted["metrics"]["wrong_rejection_reason"], 0)
        self.assertEqual(accepted["metrics"]["matched_fields"], 20)

        wrong_issue = replace(issue, code="unreadable_pdf")
        rejected = self.score_batch(BatchResult(self.batch.invoices, [wrong_issue, *self.batch.issues[1:]], 8))
        self.assertFalse(rejected["passed"])
        self.assertEqual(rejected["metrics"]["wrong_rejection_reason"], 1)
        self.assertEqual(rejected["metrics"]["incorrect_acceptance"], 0)
        self.assertEqual(rejected["metrics"]["correct_rejection"], 3)

    def test_missing_duplicate_unknown_or_miscounted_results_cannot_pass(self):
        scenarios = {
            "missing": BatchResult(self.batch.invoices[1:], self.batch.issues, 8),
            "multiple": BatchResult([*self.batch.invoices, self.batch.invoices[0]], self.batch.issues, 8),
            "unknown_source": BatchResult(
                [replace(self.batch.invoices[0], source_file="unknown.pdf"), *self.batch.invoices[1:]],
                self.batch.issues, 8),
            "wrong_count": BatchResult(self.batch.invoices, self.batch.issues, 7),
        }
        for name, batch in scenarios.items():
            with self.subTest(name=name):
                report = self.score_batch(batch)
                self.assertFalse(report["passed"])
                self.assertTrue(report["integrity_errors"])
                self.assertEqual(report["metrics"]["expected_fields"], 20)

    def test_missing_extra_and_nested_fixture_pdfs_are_errors(self):
        for scenario in ["missing", "extra", "nested"]:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                fixtures = Path(temporary) / "pdfs"
                shutil.copytree(FIXTURES, fixtures)
                if scenario == "missing":
                    (fixtures / "missing_amount.pdf").unlink()
                else:
                    extra = fixtures / "extra.pdf" if scenario == "extra" else fixtures / "nested/extra.pdf"
                    extra.parent.mkdir(exist_ok=True)
                    extra.write_bytes(b"not read: inventory validation must fail first")
                with patch("tools.evaluate.process_directory") as pipeline:
                    with self.assertRaisesRegex(ValueError, "inventory mismatch"):
                        evaluate(fixtures)
                    pipeline.assert_not_called()

    def test_empty_duplicate_or_incomplete_expectations_are_errors(self):
        original = json.loads(EXPECTED.read_text(encoding="utf-8"))
        missing_field = json.loads(json.dumps(original))
        del missing_field["cases"][0]["expected_invoice"]["total"]
        missing_code = json.loads(json.dumps(original))
        del missing_code["cases"][-1]["expected_code"]
        manifests = [
            {}, {"schema_version": 1, "cases": []},
            {"schema_version": 1, "cases": [original["cases"][0], original["cases"][0]]},
            missing_field, missing_code,
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "expected.json"
            for manifest in manifests:
                with self.subTest(manifest=manifest):
                    path.write_text(json.dumps(manifest), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_expectations(path, FIXTURES)

    def test_module_cli_saves_detailed_success_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "reports/evaluation.json"
            completed = subprocess.run(
                [sys.executable, "-m", "tools.evaluate", "--output", str(output)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(len(report["cases"]), 8)
            self.assertIn("not a real-world accuracy estimate", completed.stdout)

    def test_cli_mismatches_and_bad_inputs_have_nonzero_exit_codes(self):
        invoice = self.batch.invoices[0]
        issue = Issue(invoice.source_file, "missing_field", "Simulated extraction regression")
        batch = BatchResult(self.batch.invoices[1:], [*self.batch.issues, issue], 8)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "failed.json"
            with patch("tools.evaluate.process_directory", return_value=batch), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--output", str(output)]), 1)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["metrics"]["incorrect_rejection"], 1)
            self.assertEqual(report["metrics"]["expected_fields"], 20)

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["--fixtures", str(Path(temporary) / "missing")]), 2)
            self.assertIn("does not exist", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
