"""BGYHub Mailbox Admin: local web UI for Purelymail mailboxes.

Listens on 127.0.0.1 only. Run from the purelymail/ folder:
    python -m admin.app
The Purelymail API token stays in purelymail/.env (or the environment) and is
only ever used server-side; no response, page or log contains it.
"""

from __future__ import annotations

import hmac
import logging
import os
import platform
import secrets
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file, session, url_for

import mailbox_core as core
from admin.service import Config, MailboxService, ServiceError
from admin.store import MIN_ADMIN_PASSWORD, Store

APP_ID = "bgyhub-mailbox-admin"
HOST = "127.0.0.1"
DEFAULT_PORT = 8787
IDLE_LOCK_SECONDS = 30 * 60
REAUTH_SECONDS = 5 * 60
LOGIN_FAILURE_DELAY = 2.0

ADMIN_DIR = Path(__file__).resolve().parent

log = logging.getLogger("purelymail.admin")


class Vault:
    """The unlocked data key lives only in memory, only while logged in."""

    def __init__(self):
        self.key: bytes | None = None
        self.epoch = secrets.token_hex(8)
        self.last_seen = 0.0
        self.failures = 0

    def unlock(self, key: bytes) -> str:
        self.key, self.epoch, self.last_seen, self.failures = key, secrets.token_hex(8), time.time(), 0
        return self.epoch

    def lock(self) -> None:
        self.key, self.epoch = None, secrets.token_hex(8)


def create_app(data_dir: Path | None = None, output_dir: Path | None = None, base_url: str = core.API_BASE,
               port: int = DEFAULT_PORT, search_dirs: list | None = None) -> Flask:
    app = Flask(__name__, template_folder=str(ADMIN_DIR / "templates"), static_folder=str(ADMIN_DIR / "static"))
    app.config.update(
        SECRET_KEY=secrets.token_bytes(32),  # new per start: restarting logs everyone out
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_NAME="bgyhub_admin",
        MAX_CONTENT_LENGTH=64 * 1024,
        PORT=port,
    )
    data_dir = data_dir or ADMIN_DIR / "data"
    store = Store(data_dir / "admin.db")
    config = Config(data_dir=data_dir, output_dir=output_dir or core.OUTPUT_DIR, base_url=base_url)
    if search_dirs is not None:
        config.search_dirs = search_dirs
    service = MailboxService(store, config)
    vault = Vault()
    app.extensions["mailbox"] = {"store": store, "service": service, "vault": vault}
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    # ------------------------------------------------------------------ request guards

    @app.before_request
    def guard():
        # DNS-rebinding protection: only answer to our own loopback host name.
        if request.host not in allowed_hosts:
            abort(400)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in allowed_hosts}:
                abort(403)
            sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                abort(403)

    @app.after_request
    def headers(resp: Response):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"  # "no-referrer" makes browsers send Origin: null
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def csrf_token() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    def logged_in() -> bool:
        if vault.key is None or session.get("epoch") != vault.epoch:
            return False
        if time.time() - vault.last_seen > IDLE_LOCK_SECONDS:
            vault.lock()
            store.audit("lock", "idle")
            return False
        vault.last_seen = time.time()
        return True

    def require_login() -> bytes:
        if not logged_in():
            abort(401)
        return vault.key

    def require_reauth() -> bytes:
        key = require_login()
        if time.time() - session.get("reauth_at", 0) > REAUTH_SECONDS:
            abort(Response('{"error": "reauth"}', 428, mimetype="application/json"))
        return key

    @app.errorhandler(ServiceError)
    def service_error(e: ServiceError):
        return jsonify(error=str(e)), e.status

    @app.errorhandler(401)
    def unauthorized(_):
        return jsonify(error="locked"), 401

    # ------------------------------------------------------------------ pages

    @app.get("/healthz")
    def healthz():
        return jsonify(app=APP_ID)

    @app.get("/")
    def index():
        if not store.is_initialized():
            return redirect(url_for("setup"))
        if not logged_in():
            return redirect(url_for("login"))
        return render_template("index.html", csrf=csrf_token(), max_count=core.MAX_COUNT)

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if store.is_initialized():
            return redirect(url_for("login"))
        error = None
        if request.method == "POST":
            pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
            if pw != pw2:
                error = "The two passwords do not match."
            elif len(pw) < MIN_ADMIN_PASSWORD:
                error = f"Use at least {MIN_ADMIN_PASSWORD} characters."
            else:
                session["epoch"] = vault.unlock(store.initialize(pw))
                store.audit("setup", "ok")
                return redirect(url_for("index"))
        return render_template("login.html", mode="setup", error=error, csrf=csrf_token(),
                               min_len=MIN_ADMIN_PASSWORD)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not store.is_initialized():
            return redirect(url_for("setup"))
        error = None
        if request.method == "POST":
            key = store.unlock(request.form.get("password", ""))
            if key is None:
                vault.failures += 1
                time.sleep(min(LOGIN_FAILURE_DELAY * vault.failures, 10))
                store.audit("login", "failed")
                error = "Wrong password."
            else:
                session.clear()
                session["epoch"] = vault.unlock(key)
                session["csrf"] = secrets.token_urlsafe(32)
                store.audit("login", "ok")
                return redirect(url_for("index"))
        return render_template("login.html", mode="login", error=error, csrf=csrf_token(),
                               min_len=MIN_ADMIN_PASSWORD)

    # ------------------------------------------------------------------ API

    @app.post("/api/lock")
    def api_lock():
        vault.lock()
        session.clear()
        store.audit("lock", "manual")
        return jsonify(ok=True)

    @app.post("/api/reauth")
    def api_reauth():
        require_login()
        if store.unlock((request.get_json(silent=True) or {}).get("password", "")) is None:
            time.sleep(LOGIN_FAILURE_DELAY)
            store.audit("reauth", "failed")
            return jsonify(error="Wrong password."), 403
        session["reauth_at"] = time.time()
        return jsonify(ok=True)

    @app.get("/api/status")
    def api_status():
        require_login()
        return jsonify(service.status())

    @app.get("/api/mailboxes")
    def api_mailboxes():
        require_login()
        return jsonify(mailboxes=service.mailbox_rows())

    @app.post("/api/preview")
    def api_preview():
        require_login()
        count = (request.get_json(silent=True) or {}).get("count")
        try:
            count = int(count)
        except (TypeError, ValueError):
            raise ServiceError(f"Enter a number between 1 and {core.MAX_COUNT}") from None
        return jsonify(service.preview(count))

    @app.post("/api/create")
    def api_create():
        key = require_login()
        plan_id = (request.get_json(silent=True) or {}).get("plan_id", "")
        return jsonify(job_id=service.start_batch(plan_id, key))

    @app.get("/api/jobs/<job_id>")
    def api_job(job_id):
        require_login()
        return jsonify(service.job(job_id))

    @app.get("/api/jobs/<job_id>/export.csv")
    def api_export(job_id):
        key = require_reauth()
        body = service.export_batch_csv(job_id, key)
        return Response(body, mimetype="text/csv", headers={
            "Content-Disposition": f'attachment; filename="bgyhub-mailboxes-{job_id}.csv"'})

    @app.post("/api/mailboxes/<email>/reset-password")
    def api_reset(email):
        key = require_login()
        confirm = (request.get_json(silent=True) or {}).get("confirm", "")
        if confirm.strip().lower() != email.strip().lower():
            raise ServiceError("Type the mailbox address exactly to confirm.")
        return jsonify(service.reset_password(email, key))

    @app.post("/api/import-csv")
    def api_import():
        key = require_login()
        return jsonify(service.import_from_csv(key))

    @app.post("/api/excel/sync")
    def api_excel_sync():
        key = require_login()
        return jsonify(service.sync_excel(key))

    @app.get("/api/excel/pending")
    def api_excel_pending():
        require_login()
        return jsonify(pending=store.pending_excel())

    @app.post("/api/excel/pending/<int:pending_id>/dismiss")
    def api_excel_dismiss(pending_id):
        require_login()
        service.dismiss_pending(pending_id)
        return jsonify(ok=True)

    @app.get("/api/tracking/candidates")
    def api_candidates():
        require_login()
        return jsonify(candidates=service.find_tracking_candidates())

    @app.post("/api/tracking/select")
    def api_select():
        require_login()
        return jsonify(service.set_tracking_path((request.get_json(silent=True) or {}).get("path", "")))

    @app.post("/api/tracking/open")
    def api_open():
        require_login()
        path = service.tracking_path()
        if not path or not path.exists():
            raise ServiceError("No tracking workbook selected.", 404)
        if platform.system() != "Darwin":
            raise ServiceError("Opening files is only supported on macOS.", 501)
        subprocess.run(["open", str(path)], check=False)
        store.audit("tracking_open", "ok")
        return jsonify(ok=True)

    @app.get("/api/tracking/download")
    def api_download():
        require_reauth()
        path = service.tracking_path()
        if not path or not path.exists():
            raise ServiceError("No tracking workbook selected.", 404)
        store.audit("tracking_download", "ok")
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.get("/api/audit")
    def api_audit():
        require_login()
        return jsonify(audit=store.recent_audit(100))

    @app.post("/api/quit")
    def api_quit():
        require_login()
        store.audit("quit", "ok")
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return jsonify(ok=True)

    return app


def setup_logging(output_dir: Path) -> None:
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    handler = RotatingFileHandler(output_dir / "admin.log", maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s"))
    for name in ("purelymail", "purelymail.admin"):
        logging.getLogger(name).addHandler(handler)
        logging.getLogger(name).setLevel(logging.INFO)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no per-request noise


def main() -> None:
    port = int(os.environ.get("ADMIN_PORT", DEFAULT_PORT))
    setup_logging(core.OUTPUT_DIR)
    app = create_app(port=port)
    print(f"BGYHub Mailbox Admin running at http://127.0.0.1:{port}", file=sys.stderr)
    app.run(host=HOST, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
