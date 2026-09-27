"""Local invoice review, durable decisions, and immutable report snapshots."""

import json
import os
import re
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from .extraction import LAYOUTS, parse_invoice, read_pdf_text
from .models import BatchResult, InvoiceError, Issue
from .pipeline import process_directory
from .reporting import OUTPUT_NAMES, build_summary, write_reports


FIELD_NAMES = tuple(LAYOUTS["standard"])
EXPORT_NAMES = (*OUTPUT_NAMES, "review_decisions.json")
BLOCKED_CODES = {"unreadable_pdf", "encrypted_pdf", "no_text"}
MAX_FILES = 100
MAX_FILE_BYTES = 10 * 1024 * 1024


class ReviewError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReviewError("Invalid identifier.") from exc
    if str(parsed) != value or parsed.version != 4:
        raise ReviewError("Invalid identifier.")
    return value


def _filename(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1000:
        raise ReviewError("Each upload needs a valid PDF filename.")
    # Flatten path separators and remove special characters before writing files.
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = "_".join(value.replace("/", " ").replace("\\", " ").split())
    value = re.sub(r"[^A-Za-z0-9_.-]", "", value).strip("._")
    if not value or len(value) > 200 or Path(value).suffix.lower() != ".pdf":
        raise ReviewError("Upload PDF files with filenames of at most 200 characters.")
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
        value = "_" + value
    return value


def _fields(invoice) -> dict:
    row = invoice.as_row()
    return {field: row[field] for field in FIELD_NAMES}


def _partial_fields(text: str) -> tuple[str, dict]:
    text = "\n".join(line.strip() for line in text.splitlines())
    candidates = [name for name, labels in LAYOUTS.items() if re.search(
        rf"^{re.escape(labels['invoice_number'])}\s*:", text, re.MULTILINE | re.IGNORECASE
    )]
    values = dict.fromkeys(FIELD_NAMES, "")
    if len(candidates) != 1:
        return "manual", values
    layout = candidates[0]
    for field, label in LAYOUTS[layout].items():
        matches = re.findall(rf"^{re.escape(label)}[ \t]*:[ \t]*([^\r\n]*)$", text, re.MULTILINE | re.IGNORECASE)
        if len(matches) == 1:
            values[field] = " ".join(matches[0].split())
    return layout, values


def _validated_invoice(record: dict, fields: dict):
    if not isinstance(fields, dict) or set(fields) != set(FIELD_NAMES):
        raise ReviewError("Provide all five invoice fields.")
    for value in fields.values():
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ReviewError("Invoice fields must contain 1 to 200 characters.")
        if any(ord(character) < 32 or character in "\x7f\x85\u2028\u2029" for character in value):
            raise ReviewError("Invoice fields cannot contain newlines or control characters.")
    text = "\n".join(f"{label}: {fields[field]}" for field, label in LAYOUTS["standard"].items())
    try:
        invoice = parse_invoice(text, record["source_file"])
    except InvoiceError as exc:
        raise ReviewError(str(exc)) from exc
    edited = _fields(invoice) != record["original_fields"]
    layout = "manual" if edited else record["original_layout"]
    return replace(invoice, layout=layout)


class ReviewStore:
    """A single-user store. Every mutation checks a revision under a file lock."""

    _locks: dict[str, threading.RLock] = {}
    _guard = threading.Lock()

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self._guard:
            self._thread_lock = self._locks.setdefault(str(self.root), threading.RLock())

    @contextmanager
    def _locked(self):
        # The thread lock also covers multiple store objects in one web process;
        # the OS lock covers another local process using the same data folder.
        with self._thread_lock, (self.root / ".review.lock").open("a+b") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 5
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ReviewError("Another review operation is running. Try again.", 409) from exc
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load(self, batch_id: str) -> dict:
        path = self.root / _identifier(batch_id) / "batch.json"
        if not path.resolve().is_relative_to(self.root):
            raise ReviewError("Invalid batch path.")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ReviewError("Batch not found.", 404) from exc

    def _save(self, batch: dict, folder: Path | None = None) -> None:
        folder = folder or self.root / batch["id"]
        temporary = folder / f".batch-{uuid4()}.json"
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(batch, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(folder / "batch.json")
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _revision(batch: dict, revision: int) -> None:
        if type(revision) is not int:
            raise ReviewError("A numeric batch revision is required.")
        if revision != batch["revision"]:
            raise ReviewError("This batch changed. Refresh it before saving your decision.", 409)

    @staticmethod
    def _record(batch: dict, record_id: str) -> dict:
        _identifier(record_id)
        for record in batch["records"]:
            if record["id"] == record_id:
                return record
        raise ReviewError("Invoice record not found.", 404)

    @staticmethod
    def _result(batch: dict) -> BatchResult:
        invoices = []
        issues = []
        for record in batch["records"]:
            if record["status"] == "approved":
                invoices.append(_validated_invoice(record, record["fields"]))
            elif record["status"] == "rejected":
                issues.append(Issue(record["source_file"], "manually_rejected", record["note"]))
            else:
                issues.append(Issue(record["source_file"], record["code"] or "pending_review", record["message"] or "Awaiting approval."))
        return BatchResult(invoices, issues, len(batch["records"]))

    @staticmethod
    def _approved_keys(batch: dict) -> set:
        return {_validated_invoice(record, record["fields"]).duplicate_key for record in batch["records"] if record["status"] == "approved"}

    def _public(self, batch: dict) -> dict:
        keys = self._approved_keys(batch)
        ready = 0
        for record in batch["records"]:
            if record["status"] == "pending" and not record["code"] and record["fields"] == record["original_fields"]:
                if _validated_invoice(record, record["fields"]).duplicate_key not in keys:
                    ready += 1
        return {
            **batch,
            "counts": {**{status: sum(record["status"] == status for record in batch["records"]) for status in ("pending", "approved", "rejected")}, "ready": ready},
            "summary": build_summary(self._result(batch)),
        }

    def create(self, files: list[tuple[str, bytes]]) -> dict:
        if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
            raise ReviewError(f"Upload between 1 and {MAX_FILES} PDFs.")
        uploads = []
        names = set()
        for item in files:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ReviewError("Invalid upload.")
            original_name, content = item
            name = _filename(original_name)
            if name.casefold() in names:
                raise ReviewError("Uploaded filenames must remain unique after sanitizing.")
            if not isinstance(content, bytes) or not 1 <= len(content) <= MAX_FILE_BYTES:
                raise ReviewError("Each PDF must contain data and be at most 10 MB.")
            names.add(name.casefold())
            uploads.append((name, original_name, content))
        with self._locked(), TemporaryDirectory(prefix=".incoming-", dir=self.root) as temporary:
            batch_id = str(uuid4())
            folder = Path(temporary) / batch_id
            sources = folder / "sources"
            sources.mkdir(parents=True)
            originals = {}
            for name, original_name, content in uploads:
                (sources / name).write_bytes(content)
                originals[name] = original_name
            processed = process_directory(sources)
            issues = {issue.source_file: issue for issue in processed.issues}
            records = []
            for path in sorted(sources.iterdir(), key=lambda path: path.name):
                text = ""
                try:
                    text = read_pdf_text(path)
                    invoice = parse_invoice(text, path.name)
                    layout, fields = invoice.layout, _fields(invoice)
                except InvoiceError:
                    layout, fields = _partial_fields(text)
                issue = issues.get(path.name)
                if issue is None:
                    try:
                        _validated_invoice({"source_file": path.name, "original_fields": fields, "original_layout": layout}, fields)
                    except ReviewError as exc:
                        code = "validation_length" if any(len(value) > 200 for value in fields.values()) else "invalid_field"
                        issue = Issue(path.name, code, str(exc))
                records.append({
                    "id": str(uuid4()), "source_file": path.name, "uploaded_name": originals[path.name],
                    "fields": fields.copy(), "original_fields": fields.copy(), "layout": layout,
                    "original_layout": layout, "status": "pending", "code": issue.code if issue else "",
                    "message": issue.message if issue else "", "note": "", "history": [], "extracted_text": text,
                })
            batch = {"id": batch_id, "created_at": _now(), "revision": 0, "records": records, "exports": []}
            public = self._public(batch)
            self._save(batch, folder)
            folder.rename(self.root / batch_id)
            return public

    def list_batches(self) -> list[dict]:
        batches = []
        for path in self.root.iterdir():
            if not path.is_dir() or path.name.startswith("."):
                continue
            try:
                batch = self.get(path.name)
            except ReviewError:
                continue
            batches.append({key: batch[key] for key in ("id", "created_at", "revision", "counts")})
        return sorted(batches, key=lambda batch: (batch["created_at"], batch["id"]), reverse=True)

    def get(self, batch_id: str) -> dict:
        return self._public(self._load(batch_id))

    @staticmethod
    def _history(record: dict, action: str, note: str) -> None:
        record["note"] = note
        record["history"].append({"action": action, "note": note, "fields": record["fields"].copy(), "timestamp": _now()})

    def decide(self, batch_id: str, record_id: str, action: str, fields: dict | None, note: str, revision: int) -> dict:
        if not isinstance(note, str) or len(note) > 2000:
            raise ReviewError("Decision notes must be text of at most 2000 characters.")
        note = note.strip()
        if action not in ("approve", "reject", "reopen"):
            raise ReviewError("Choose approve, reject, or reopen.")
        with self._locked():
            batch = self._load(batch_id)
            self._revision(batch, revision)
            record = self._record(batch, record_id)
            if action == "reopen":
                if record["status"] == "pending":
                    raise ReviewError("This invoice is already pending review.", 409)
                record["status"] = "pending"
            else:
                if record["status"] != "pending":
                    raise ReviewError("Reopen this invoice before changing its decision.", 409)
                if action == "reject":
                    if not note:
                        raise ReviewError("Explain why this invoice is rejected.")
                    record["status"] = "rejected"
                else:
                    if record["code"] in BLOCKED_CODES:
                        raise ReviewError("This document could not be read reliably. Reject it and upload a readable PDF.")
                    invoice = _validated_invoice(record, record["fields"] if fields is None else fields)
                    corrected = _fields(invoice)
                    if (record["code"] or corrected != record["original_fields"]) and not note:
                        raise ReviewError("Explain the correction or why this flagged invoice is approved.")
                    if invoice.duplicate_key in self._approved_keys(batch):
                        raise ReviewError("An invoice with this supplier and invoice number is already approved. Reopen or reject that record first.", 409)
                    record.update(fields=corrected, layout=invoice.layout, status="approved")
            self._history(record, action, note)
            batch["revision"] += 1
            self._save(batch)
            return self._public(batch)

    def approve_ready(self, batch_id: str, revision: int) -> dict:
        with self._locked():
            batch = self._load(batch_id)
            self._revision(batch, revision)
            keys = self._approved_keys(batch)
            changed = False
            for record in batch["records"]:
                if record["status"] != "pending" or record["code"]:
                    continue
                invoice = _validated_invoice(record, record["fields"])
                if invoice.duplicate_key in keys:
                    continue
                # Previously corrected, reopened records require another explicit
                # decision; bulk approval never re-applies a manual correction.
                if record["fields"] != record["original_fields"]:
                    continue
                keys.add(invoice.duplicate_key)
                record["status"] = "approved"
                self._history(record, "approve", "Approved unchanged with the ready invoices.")
                changed = True
            if changed:
                batch["revision"] += 1
                self._save(batch)
            return self._public(batch)

    def export(self, batch_id: str, revision: int) -> dict:
        with self._locked():
            batch = self._load(batch_id)
            self._revision(batch, revision)
            if any(record["status"] == "pending" for record in batch["records"]):
                raise ReviewError("Approve or reject every invoice before exporting.", 409)
            export_id = str(uuid4())
            exports = self.root / batch_id / "exports"
            exports.mkdir(exist_ok=True)
            created_at = _now()
            with TemporaryDirectory(prefix=".export-", dir=exports) as temporary:
                reports = Path(temporary) / "reports"
                try:
                    summary = write_reports(self._result(batch), reports, reviewed=True)
                    audit = {"batch_id": batch_id, "export_id": export_id, "revision": revision, "created_at": created_at, "records": batch["records"]}
                    (reports / "review_decisions.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                    reports.rename(exports / export_id)
                except OSError as exc:
                    raise ReviewError("Reports could not be saved. No export was published.", 500) from exc
            batch["exports"].append({"id": export_id, "revision": revision, "created_at": created_at})
            self._save(batch)
            return {"export_id": export_id, "revision": revision, "summary": summary}

    def _file(self, path: Path) -> Path:
        if not path.resolve().is_relative_to(self.root) or not path.is_file():
            raise ReviewError("File not found.", 404)
        return path

    def pdf_path(self, batch_id: str, record_id: str) -> Path:
        batch = self._load(batch_id)
        record = self._record(batch, record_id)
        return self._file(self.root / batch_id / "sources" / record["source_file"])

    def export_path(self, batch_id: str, export_id: str, name: str) -> Path:
        _identifier(export_id)
        if name not in EXPORT_NAMES:
            raise ReviewError("Unknown report file.", 404)
        batch = self._load(batch_id)
        if not any(item["id"] == export_id for item in batch["exports"]):
            raise ReviewError("Export not found.", 404)
        return self._file(self.root / batch_id / "exports" / export_id / name)
