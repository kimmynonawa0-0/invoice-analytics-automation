"""Business rules for durable local review and approved report snapshots."""

import csv
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from pypdf import PdfWriter

from invoice_analytics.review import EXPORT_NAMES, MAX_FILE_BYTES, ReviewError, ReviewStore
from tools.generate_samples import render_pdf


STANDARD = "Invoice Number: INV-001\nSupplier: Example Supplier\nInvoice Date: 2026-09-01\nCurrency: SGD\nTotal: 125.50"


class ReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.store = ReviewStore(self.folder / "review")

    def pdf(self, text=STANDARD):
        path = self.folder / "fixture.pdf"
        render_pdf(path, text.splitlines())
        return path.read_bytes()

    def create(self, *texts):
        return self.store.create([(f"{index}.pdf", self.pdf(text)) for index, text in enumerate(texts or [STANDARD])])

    def decide(self, batch, index, action="approve", fields=None, note=""):
        return self.store.decide(batch["id"], batch["records"][index]["id"], action, fields, note, batch["revision"])

    def assert_review_error(self, status, function, *args, **kwargs):
        with self.assertRaises(ReviewError) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.status, status)

    def test_correction_rejection_reopening_and_source_preservation(self):
        batch = self.create(STANDARD.replace("Total: 125.50", "Total:"), STANDARD.replace("INV-001", "INV-002"))
        self.assertEqual(batch["revision"], 0)
        self.assertEqual(batch["counts"], {"pending": 2, "approved": 0, "rejected": 0, "ready": 1})
        record = batch["records"][0]
        source_path = self.store.pdf_path(batch["id"], record["id"])
        source_bytes = source_path.read_bytes()
        self.assertEqual(record["fields"]["supplier"], "Example Supplier")
        self.assertEqual(record["fields"]["total"], "")
        corrected = {**record["fields"], "total": "49.90"}
        self.assert_review_error(400, self.decide, batch, 0, fields=corrected)
        batch = self.decide(batch, 0, fields=corrected, note="Read total from the source document.")
        record = batch["records"][0]
        self.assertEqual(record["status"], "approved")
        self.assertEqual(record["layout"], "manual")
        self.assertEqual(record["code"], "missing_field")
        self.assertEqual(record["original_fields"]["total"], "")
        self.assertEqual(batch["summary"]["currencies"]["SGD"]["total"], "49.90")
        self.assert_review_error(400, self.decide, batch, 1, "reject")
        batch = self.decide(batch, 1, "reject", note="Invoice belongs to another account.")
        self.assertEqual(batch["counts"]["rejected"], 1)
        self.assert_review_error(409, self.decide, batch, 0)
        batch = self.decide(batch, 0, "reopen", note="Check the corrected total again.")
        self.assertEqual(batch["counts"]["pending"], 1)
        self.assertEqual(batch["summary"]["currencies"], {})
        self.assertEqual([entry["action"] for entry in batch["records"][0]["history"]], ["approve", "reopen"])
        self.assertEqual(source_path.read_bytes(), source_bytes)

    def test_ambiguous_fields_are_blank_and_unsupported_layout_can_be_entered(self):
        batch = self.create(STANDARD + "\nTotal: 999.00", "CUSTOM INVOICE\nAmount payable is 23.00")
        ambiguous, unsupported = batch["records"]
        self.assertEqual(ambiguous["code"], "ambiguous_field")
        self.assertEqual(ambiguous["fields"]["total"], "")
        self.assertEqual(ambiguous["fields"]["invoice_number"], "INV-001")
        self.assertEqual(unsupported["code"], "unsupported_layout")
        self.assertTrue(all(value == "" for value in unsupported["fields"].values()))
        self.assertEqual(self.store.approve_ready(batch["id"], batch["revision"])["counts"]["approved"], 0)
        fields = {"invoice_number": "CUSTOM-1", "supplier": "Custom Supplier", "invoice_date": "2026-09-01", "currency": "SGD", "total": "23.00"}
        batch = self.decide(batch, 1, fields=fields, note="Entered all values after comparing with the PDF.")
        self.assertEqual(batch["records"][1]["layout"], "manual")
        self.assertEqual(batch["summary"]["accepted_invoices"], 1)

    def test_unreadable_blank_and_encrypted_documents_cannot_be_approved(self):
        def generated(encrypted=False):
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=200)
            if encrypted:
                writer.encrypt("secret")
            output = io.BytesIO()
            writer.write(output)
            return output.getvalue()

        batch = self.store.create([("a.pdf", b"not a PDF"), ("b.pdf", generated()), ("c.pdf", generated(True))])
        self.assertEqual([record["code"] for record in batch["records"]], ["unreadable_pdf", "no_text", "encrypted_pdf"])
        good_fields = self.create()["records"][0]["fields"]
        for index in range(3):
            self.assert_review_error(400, self.decide, batch, index, fields=good_fields, note="Attempt a manual approval.")
        self.assertEqual(self.store.get(batch["id"])["revision"], 0)

    def test_conflict_selection_and_corrected_identity_cannot_double_count(self):
        batch = self.create(STANDARD, STANDARD, STANDARD.replace("125.50", "999.00"))
        self.assertEqual([record["code"] for record in batch["records"]], ["conflicting_duplicate"] * 3)
        self.assertEqual(self.store.approve_ready(batch["id"], 0)["revision"], 0)
        self.assert_review_error(400, self.decide, batch, 2)
        batch = self.decide(batch, 2, note="Verified the revised invoice with the supplier.")
        self.assert_review_error(409, self.decide, batch, 0, note="Would duplicate the approved invoice.")
        changed_case = {**batch["records"][0]["fields"], "supplier": "example   supplier", "invoice_number": "inv-001"}
        self.assert_review_error(409, self.decide, batch, 0, fields=changed_case, note="Same identity after normalization.")
        new_identity = {**batch["records"][0]["fields"], "invoice_number": "INV-002"}
        batch = self.decide(batch, 0, fields=new_identity, note="Corrected invoice number from the original.")
        batch = self.decide(batch, 1, "reject", note="Duplicate of approved invoice.")
        self.assertEqual(batch["summary"]["accepted_invoices"], 2)
        self.assertEqual(batch["summary"]["currencies"]["SGD"]["total"], "1124.50")

    def test_exact_duplicate_chosen_manually_blocks_bulk_acceptance_of_first(self):
        batch = self.create(STANDARD, STANDARD)
        self.assertEqual(batch["counts"]["ready"], 1)
        batch = self.decide(batch, 1, note="This is the preferred source copy.")
        self.assertEqual(batch["counts"]["ready"], 0)
        unchanged = self.store.approve_ready(batch["id"], batch["revision"])
        self.assertEqual(unchanged["revision"], batch["revision"])
        self.assertEqual(unchanged["counts"]["approved"], 1)
        self.assert_review_error(409, self.decide, batch, 0)

    def test_bulk_approval_and_reopened_corrections(self):
        batch = self.create()
        batch = self.store.approve_ready(batch["id"], 0)
        self.assertEqual(batch["revision"], 1)
        self.assertEqual(batch["counts"]["approved"], 1)
        self.assertEqual(batch["records"][0]["history"][0]["action"], "approve")
        batch = self.decide(batch, 0, "reopen")
        batch = self.decide(batch, 0, fields={**batch["records"][0]["fields"], "total": "90.00"}, note="Verified corrected total.")
        batch = self.decide(batch, 0, "reopen")
        self.assertEqual(batch["counts"]["ready"], 0)
        self.assertEqual(self.store.approve_ready(batch["id"], batch["revision"])["revision"], batch["revision"])

    def test_oversized_extracted_fields_remain_reviewable(self):
        batch = self.create(STANDARD.replace("Example Supplier", "A" * 201), STANDARD.replace("INV-001", "INV-002"))
        self.assertEqual(batch["records"][0]["code"], "validation_length")
        self.assertEqual(batch["counts"]["ready"], 1)
        self.assertEqual(self.store.get(batch["id"]), batch)
        self.assertEqual(len(self.store.list_batches()), 1)
        corrected = {**batch["records"][0]["fields"], "supplier": "Verified Supplier"}
        batch = self.decide(batch, 0, fields=corrected, note="Corrected the oversized supplier label.")
        self.assertEqual(batch["counts"]["approved"], 1)

    def test_strict_field_validation_and_stale_revisions(self):
        batch = self.create()
        original = batch["records"][0]["fields"]
        for field, value in [("supplier", "Injected\nTotal: 1"), ("supplier", "a" * 201), ("supplier", ""), ("invoice_date", "2026-02-30"), ("total", "0"), ("total", "NaN"), ("currency", "JPY")]:
            with self.subTest(field=field, value=value):
                self.assert_review_error(400, self.decide, batch, 0, fields={**original, field: value}, note="Invalid input.")
        self.assert_review_error(400, self.decide, batch, 0, fields={"total": "10"}, note="Missing other fields.")
        self.assert_review_error(400, self.store.approve_ready, batch["id"], True)
        self.assert_review_error(400, self.store.approve_ready, batch["id"], None)
        approved = self.decide(batch, 0)
        self.assert_review_error(409, self.decide, batch, 0, "reject", note="Stale decision.")
        self.assert_review_error(409, self.store.approve_ready, batch["id"], 0)
        self.assert_review_error(409, self.store.export, batch["id"], 0)
        restored = ReviewStore(self.store.root).get(batch["id"])
        self.assertEqual(restored, approved)
        self.assertEqual(self.store.list_batches()[0]["counts"]["approved"], 1)

    def test_exports_require_complete_review_and_preserve_old_snapshots(self):
        batch = self.create(STANDARD, STANDARD.replace("INV-001", "INV-002"))
        self.assert_review_error(409, self.store.export, batch["id"], 0)
        batch = self.decide(batch, 0)
        self.assert_review_error(409, self.store.export, batch["id"], batch["revision"])
        batch = self.decide(batch, 1, "reject", note="Belongs to another account.")
        first = self.store.export(batch["id"], batch["revision"])
        self.assertEqual(first["revision"], batch["revision"])
        self.assertEqual(self.store.get(batch["id"])["revision"], batch["revision"])
        self.assertEqual(first["summary"]["accepted_invoices"], 1)
        self.assertEqual(first["summary"]["review_files"], 1)
        snapshots = {name: self.store.export_path(batch["id"], first["export_id"], name).read_bytes() for name in EXPORT_NAMES}
        with self.store.export_path(batch["id"], first["export_id"], "invoices.csv").open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual([row["invoice_number"] for row in csv.DictReader(handle)], ["INV-001"])
        with self.store.export_path(batch["id"], first["export_id"], "review.csv").open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(next(csv.DictReader(handle))["code"], "manually_rejected")
        audit = json.loads(snapshots["review_decisions.json"])
        self.assertEqual(audit["records"][1]["note"], "Belongs to another account.")
        batch = self.decide(self.store.get(batch["id"]), 0, "reopen")
        batch = self.decide(batch, 0, fields={**batch["records"][0]["fields"], "total": "50.00"}, note="Updated after checking the source.")
        second = self.store.export(batch["id"], batch["revision"])
        self.assertNotEqual(first["export_id"], second["export_id"])
        self.assertEqual(second["summary"]["currencies"]["SGD"]["total"], "50.00")
        for name, content in snapshots.items():
            self.assertEqual(self.store.export_path(batch["id"], first["export_id"], name).read_bytes(), content)
        self.assertEqual(len(ReviewStore(self.store.root).get(batch["id"])["exports"]), 2)

    def test_competing_store_instances_cannot_overwrite_decisions(self):
        batch = self.create()
        stores = [self.store, ReviewStore(self.store.root)]

        def decide(store, action):
            try:
                store.decide(batch["id"], batch["records"][0]["id"], action, None, "Reviewed document.", 0)
                return 200
            except ReviewError as exc:
                return exc.status

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(decide, store, action) for store, action in zip(stores, ["approve", "reject"])]
            self.assertEqual(sorted(future.result() for future in futures), [200, 409])
        restored = self.store.get(batch["id"])
        self.assertEqual(restored["revision"], 1)
        self.assertEqual(len(restored["records"][0]["history"]), 1)

    def test_all_rejected_batch_can_export(self):
        batch = self.decide(self.create(), 0, "reject", note="Wrong company.")
        exported = self.store.export(batch["id"], batch["revision"])
        self.assertEqual(exported["summary"]["accepted_invoices"], 0)
        self.assertEqual(exported["summary"]["currencies"], {})
        self.assertTrue(self.store.export_path(batch["id"], exported["export_id"], "dashboard.html").is_file())

    def test_failed_report_generation_is_not_published(self):
        batch = self.decide(self.create(), 0)

        def fail(result, path, **kwargs):
            path.mkdir()
            (path / "invoices.csv").write_text("partial output")
            raise OSError("Disk full")

        with patch("invoice_analytics.review.write_reports", side_effect=fail):
            self.assert_review_error(500, self.store.export, batch["id"], batch["revision"])
        restored = self.store.get(batch["id"])
        self.assertEqual(restored["exports"], [])
        self.assertEqual(restored["revision"], batch["revision"])
        self.assertEqual(list((self.store.root / batch["id"] / "exports").iterdir()), [])

    def test_invalid_uploads_ids_and_report_names(self):
        for files in [[], [("test.txt", b"hello")], [("empty.pdf", b"")], [("huge.pdf", b"x" * (MAX_FILE_BYTES + 1))], [("x.pdf", "not bytes")], [("x.pdf", b"x")] * 101, [("a b.pdf", b"x"), ("a_b.pdf", b"x")], [("UPPER.pdf", b"x"), ("upper.PDF", b"x")]]:
            with self.subTest(names=[item[0] for item in files][:2]):
                self.assert_review_error(400, self.store.create, files)
        self.assertEqual(self.store.list_batches(), [])
        batch = self.store.create([("../../invoice.pdf", self.pdf())])
        self.assertEqual(batch["records"][0]["source_file"], "invoice.pdf")
        self.assertTrue(self.store.pdf_path(batch["id"], batch["records"][0]["id"]).resolve().is_relative_to(self.store.root))
        for identifier in ["../outside", "not-a-uuid", batch["id"].upper(), None]:
            self.assert_review_error(400, self.store.get, identifier)
            self.assert_review_error(400, self.store.pdf_path, batch["id"], identifier)
        self.assert_review_error(404, self.store.get, str(uuid4()))
        self.assert_review_error(404, self.store.pdf_path, batch["id"], str(uuid4()))
        self.assert_review_error(404, self.store.export_path, batch["id"], str(uuid4()), "summary.json")
        self.assert_review_error(404, self.store.export_path, batch["id"], str(uuid4()), "../../batch.json")


if __name__ == "__main__":
    unittest.main()
