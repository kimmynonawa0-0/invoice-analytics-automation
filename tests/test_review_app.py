"""HTTP workflow checks for the local invoice review application."""

import csv
import io
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from zipfile import ZipFile

from invoice_analytics.review_app import create_app


ROOT = Path(__file__).resolve().parents[1]


class TokenParser(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and values.get("name") == "review-token":
            self.token = values["content"]


class ReviewAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.storage = Path(self.temporary.name) / "review"
        self.app = create_app(self.storage, ROOT / "data/samples")
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        parser = TokenParser()
        parser.feed(page.get_data(as_text=True))
        self.assertTrue(parser.token)
        self.headers = {"X-Review-Token": parser.token}

    def post(self, url, body):
        return self.client.post(url, json=body, headers=self.headers)

    def upload(self, names):
        files = [(io.BytesIO((ROOT / "data/samples" / name).read_bytes()), name) for name in names]
        response = self.client.post("/api/batches", data={"files": files}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def test_explicit_review_then_export_and_download(self):
        batch = self.upload(["invoice_001.pdf", "z_duplicate.pdf"])
        url = f"/api/batches/{batch['id']}"
        self.assertEqual(batch["counts"]["pending"], 2)
        self.assertEqual(batch["summary"]["accepted_invoices"], 0)
        self.assertEqual(self.post(url + "/export", {"revision": batch["revision"]}).status_code, 409)
        batch = self.post(url + "/approve-ready", {"revision": batch["revision"]}).get_json()
        self.assertEqual(batch["counts"]["approved"], 1)
        duplicate = next(record for record in batch["records"] if record["code"] == "duplicate_invoice")
        response = self.post(url + f"/records/{duplicate['id']}", {
            "action": "reject", "note": "Duplicate of invoice_001.pdf", "revision": batch["revision"],
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        batch = response.get_json()
        response = self.post(url + "/export", {"revision": batch["revision"]})
        self.assertEqual(response.status_code, 200, response.get_json())
        exported = response.get_json()
        self.assertEqual(exported["summary"]["currencies"]["SGD"]["total"], "125.50")
        export_url = url + f"/exports/{exported['export_id']}"
        response = self.client.get(export_url + "/download.zip")
        self.assertEqual(response.status_code, 200)
        with ZipFile(io.BytesIO(response.data)) as archive:
            self.assertEqual(set(archive.namelist()), {
                "invoices.csv", "review.csv", "summary.json", "spend_report.xlsx",
                "dashboard.html", "review_decisions.json",
            })
            rows = list(csv.DictReader(io.StringIO(archive.read("invoices.csv").decode("utf-8-sig"))))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_file"], "invoice_001.pdf")
            self.assertIn("Duplicate of invoice_001.pdf", archive.read("review_decisions.json").decode())
        dashboard = self.client.get(export_url + "/dashboard.html")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Rejected invoices", dashboard.get_data(as_text=True))
        self.assertNotIn("Files requiring review", dashboard.get_data(as_text=True))
        dashboard.close()
        self.assertEqual(self.client.get(export_url + "/batch.json").status_code, 404)
        source = next(record for record in batch["records"] if record["source_file"] == "invoice_001.pdf")
        preview = self.client.get(url + f"/pdf/{source['id']}")
        self.assertEqual(preview.mimetype, "application/pdf")
        self.assertEqual(preview.data, (ROOT / "data/samples/invoice_001.pdf").read_bytes())
        preview.close()
        # A fresh server instance reads the saved decisions and published exports.
        reloaded = create_app(self.storage).test_client().get(url).get_json()
        self.assertEqual(reloaded["counts"], batch["counts"])
        self.assertEqual(reloaded["revision"], exported["revision"])
        self.assertEqual(len(reloaded["exports"]), 1)

    def test_correction_error_preserves_state_and_stale_revision_is_rejected(self):
        batch = self.upload(["z_missing_total.pdf"])
        record = batch["records"][0]
        url = f"/api/batches/{batch['id']}"
        fields = dict(record["fields"], total="-2")
        body = {"action": "approve", "fields": fields, "note": "Checked source", "revision": batch["revision"]}
        response = self.post(url + f"/records/{record['id']}", body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())
        self.assertEqual(self.client.get(url).get_json()["revision"], batch["revision"])
        body["fields"]["total"] = "42.50"
        response = self.post(url + f"/records/{record['id']}", body)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["summary"]["currencies"]["SGD"]["total"], "42.50")
        self.assertEqual(self.post(url + "/export", {"revision": batch["revision"]}).status_code, 409)

    def test_local_page_token_host_and_upload_validation(self):
        self.assertEqual(self.client.post("/api/demo").status_code, 403)
        self.assertEqual(self.client.post("/api/demo", headers={"X-Review-Token": "wrong"}).status_code, 403)
        self.assertEqual(self.client.get("/", base_url="http://untrusted.example").status_code, 400)
        response = self.client.post("/api/batches", data={"files": (io.BytesIO(b"x"), "notes.txt")}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/batches").get_json(), {"batches": []})
        # The HTTP request cap is enforced before bytes reach storage.
        self.app.config["MAX_CONTENT_LENGTH"] = 50
        response = self.client.post("/api/batches", data={"files": (io.BytesIO(b"x" * 100), "a.pdf")}, headers=self.headers)
        self.assertEqual(response.status_code, 413)
        self.assertIn("error", response.get_json())

    def test_demo_and_bad_json_have_clear_results(self):
        response = self.client.post("/api/demo", headers=self.headers)
        self.assertEqual(response.status_code, 201, response.get_json())
        batch = response.get_json()
        self.assertEqual(batch["counts"]["pending"], 18)
        self.assertEqual(batch["counts"]["ready"], 12)
        url = f"/api/batches/{batch['id']}/approve-ready"
        self.assertEqual(self.post(url, {"revision": True}).status_code, 400)
        self.assertEqual(self.post(url, []).status_code, 400)
        self.assertEqual(self.client.get("/api/batches/not-an-id").status_code, 400)
        response = self.client.get("/static/review.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        response.close()


if __name__ == "__main__":
    unittest.main()
