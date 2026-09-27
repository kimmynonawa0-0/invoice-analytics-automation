"""Local browser interface for reviewing invoices before exporting reports."""

import argparse
import hmac
import io
import secrets
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from .reporting import OUTPUT_NAMES
from .review import ReviewError, ReviewStore


EXPORT_FILES = (*OUTPUT_NAMES, "review_decisions.json")


def create_app(data_dir: Path | None = None, demo_dir: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
        MAX_FORM_PARTS=110,
        TRUSTED_HOSTS=["127.0.0.1", "localhost"],
    )
    store = ReviewStore(data_dir or Path.cwd() / ".invoice-review")
    demo = demo_dir or Path.cwd() / "data/samples"
    token = secrets.token_urlsafe(32)
    app.extensions["review_store"] = store

    @app.before_request
    def verify_local_action():
        if request.method == "POST":
            provided = request.headers.get("X-Review-Token", "")
            if not provided.isascii() or not hmac.compare_digest(provided, token):
                raise ReviewError("This review page has expired. Reload the page and try again.", 403)

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ReviewError)
    def review_error(exc):
        return jsonify(error=str(exc)), exc.status

    @app.errorhandler(HTTPException)
    def http_error(exc):
        message = "The upload exceeds the 50 MB batch limit." if exc.code == 413 else exc.description
        return jsonify(error=message), exc.code

    @app.errorhandler(OSError)
    def storage_error(exc):
        app.logger.exception("Local review storage failed")
        return jsonify(error="Could not read or save local files. Check the storage folder permissions and retry."), 500

    @app.errorhandler(500)
    def unexpected_error(exc):
        return jsonify(error="The review operation failed. Check the terminal for details."), 500

    def payload():
        body = request.get_json()
        if not isinstance(body, dict):
            raise ReviewError("Expected a JSON object.")
        revision = body.get("revision")
        if type(revision) is not int or revision < 0:
            raise ReviewError("A valid review revision is required. Reload the batch.")
        return body

    @app.get("/")
    def index():
        return render_template("review.html", review_token=token)

    @app.get("/api/batches")
    def list_batches():
        return jsonify(batches=store.list_batches())

    @app.get("/api/batches/<batch_id>")
    def get_batch(batch_id):
        return jsonify(store.get(batch_id))

    @app.post("/api/batches")
    def upload_batch():
        uploads = request.files.getlist("files")
        if not uploads or len(uploads) > 100:
            raise ReviewError("Select between 1 and 100 PDF files.")
        files = []
        for upload in uploads:
            content = upload.read(10 * 1024 * 1024 + 1)
            if len(content) > 10 * 1024 * 1024:
                raise ReviewError("Each PDF must be at most 10 MB.", 413)
            files.append((upload.filename or "", content))
        return jsonify(store.create(files)), 201

    @app.post("/api/demo")
    def demo_batch():
        if not demo.is_dir():
            raise ReviewError("Demo PDFs are unavailable. Run from the repository root or upload your own PDFs.", 404)
        files = [(path.name, path.read_bytes()) for path in sorted(demo.iterdir())
                 if path.is_file() and path.suffix.lower() == ".pdf"]
        return jsonify(store.create(files)), 201

    @app.post("/api/batches/<batch_id>/records/<record_id>")
    def decide(batch_id, record_id):
        body = payload()
        return jsonify(store.decide(batch_id, record_id, body.get("action"), body.get("fields"),
                                    body.get("note", ""), body["revision"]))

    @app.post("/api/batches/<batch_id>/approve-ready")
    def approve_ready(batch_id):
        body = payload()
        return jsonify(store.approve_ready(batch_id, body["revision"]))

    @app.post("/api/batches/<batch_id>/export")
    def export(batch_id):
        body = payload()
        return jsonify(store.export(batch_id, body["revision"]))

    @app.get("/api/batches/<batch_id>/pdf/<record_id>")
    def preview_pdf(batch_id, record_id):
        return send_file(store.pdf_path(batch_id, record_id), mimetype="application/pdf")

    @app.get("/api/batches/<batch_id>/exports/<export_id>/download.zip")
    def download_bundle(batch_id, export_id):
        buffer = io.BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            for name in EXPORT_FILES:
                archive.write(store.export_path(batch_id, export_id, name), arcname=name)
        buffer.seek(0)
        return send_file(buffer, mimetype="application/zip", as_attachment=True,
                         download_name=f"invoice-report-{export_id[:8]}.zip")

    @app.get("/api/batches/<batch_id>/exports/<export_id>/<name>")
    def download_report(batch_id, export_id, name):
        if name not in EXPORT_FILES:
            raise ReviewError("Report file not found.", 404)
        return send_file(store.export_path(batch_id, export_id, name),
                         as_attachment=name != "dashboard.html", download_name=name)

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="Local port (default: 8765)")
    parser.add_argument("--data-dir", type=Path, default=Path(".invoice-review"), help="Local review storage")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    app = create_app(args.data_dir)
    print(f"Invoice review: http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
