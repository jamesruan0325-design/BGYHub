"""Mailbox operations for the admin app: preview, batch create, reset, import, Excel sync.

Rules carried over from the CLI:
  * existing Purelymail users are never modified, except a password reset of a
    mailbox this tool created or imported (never protected/excluded ones);
  * createUser is never retried when its outcome is ambiguous;
  * a password reaches the tracking workbook only after Purelymail confirmed it.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import mailbox_core as core
import tracking_xlsx
from admin.store import Store, now

log = logging.getLogger("purelymail.admin")

PLAN_TTL = 600  # seconds a preview stays valid
DEFAULT_SEARCH_DIRS = ["~/BGYHub", "~/Desktop", "~/Documents", "~/Downloads",
                       "~/Library/Mobile Documents/com~apple~CloudDocs"]


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Config:
    data_dir: Path
    output_dir: Path
    base_url: str = core.API_BASE
    search_dirs: list = field(default_factory=lambda: list(DEFAULT_SEARCH_DIRS))

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "tracking.lock"

    @property
    def credentials_csv(self) -> Path:
        return self.output_dir / "credentials.csv"


def _scrub(message: str, *secret_values: str) -> str:
    for s in secret_values:
        if s:
            message = message.replace(s, "***")
    return message


def _local_date(iso: str | None) -> str:
    if not iso:
        return dt.date.today().isoformat()
    try:
        stamp = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return dt.date.today().isoformat()
    if stamp.tzinfo:
        stamp = stamp.astimezone()
    return stamp.date().isoformat()


class MailboxService:
    def __init__(self, store: Store, config: Config):
        self.store = store
        self.config = config
        self._client: core.PurelymailClient | None = None
        self._client_token: str | None = None
        self._api_lock = threading.Lock()
        self._batch_lock = threading.Lock()
        self._excel_lock = threading.Lock()
        self._plans: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}

    # ------------------------------------------------------------------ Purelymail access

    def token_configured(self) -> bool:
        try:
            core.load_token()
            return True
        except core.FatalError:
            return False

    def _api(self, method: str, *args):
        """Serialised API call (the client's rate limiter is not thread-safe)."""
        with self._api_lock:
            try:
                token = core.load_token()
            except core.FatalError as e:
                raise ServiceError(str(e), 503) from None
            if self._client is None or token != self._client_token:
                self._client = core.PurelymailClient(token, self.config.base_url)
                self._client_token = token
            return getattr(self._client, method)(*args)

    def existing_users(self) -> set[str]:
        try:
            users = self._api("list_users")
        except core.ApiError as e:
            raise ServiceError(f"Could not list Purelymail users: {e}", 502) from None
        except core.FatalError as e:
            raise ServiceError(str(e), 502) from None
        return {u for u in users if u.endswith("@" + core.DOMAIN)}

    # ------------------------------------------------------------------ status / listing

    def tracking_path(self) -> Path | None:
        p = self.store.get_setting("tracking_path")
        return Path(p) if p else None

    def tracking_info(self) -> dict:
        p = self.tracking_path()
        if not p:
            return {"configured": False}
        info = tracking_xlsx.describe(p)
        info.pop("emails", None)
        return {"configured": True, **info}

    def csv_import_status(self) -> dict:
        path = self.config.credentials_csv
        done = self.store.get_setting("csv_import_done")
        managed = {m["email"] for m in self.store.list_mailboxes()}
        candidates = [e for e in self._csv_created_rows(path) if e not in managed] if path.exists() else []
        return {"path": str(path), "exists": path.exists(), "done": done,
                "available": bool(candidates) and not done, "candidates": candidates}

    def status(self) -> dict:
        return {
            "token_configured": self.token_configured(),
            "tracking": self.tracking_info(),
            "excel_pending": len(self.store.pending_excel()),
            "csv_import": self.csv_import_status(),
            "batch_running": self._batch_lock.locked(),
        }

    def mailbox_rows(self) -> list[dict]:
        existing = self.existing_users()
        managed = {m["email"]: m for m in self.store.list_mailboxes()}
        wb_emails = {}
        p = self.tracking_path()
        if p:
            wb_emails = tracking_xlsx.describe(p).get("emails", {})
        rows = []
        attention = {e for e, m in managed.items() if m["status"] == "unknown"}
        for email in sorted(existing | attention):
            m = managed.get(email)
            if email in core.PROTECTED:
                kind = "protected"
            elif email in core.EXCLUDE_FROM_SEQUENCE:
                kind = "excluded"
            elif m and m["status"] == "active":
                kind = "managed"
            elif m and m["status"] == "unknown":
                kind = "unknown"
            else:
                kind = "other"
            wb = wb_emails.get(email, {})
            rows.append({
                "email": email, "on_purelymail": email in existing, "kind": kind,
                "source": m["source"] if m else None, "created_at": m["created_at"] if m else None,
                "in_workbook": bool(wb), "workbook_has_password": wb.get("has_password", False),
                "workbook_status": wb.get("status"),
                "can_reset": kind == "managed" and email in existing,
            })
        return rows

    # ------------------------------------------------------------------ preview + batch create

    def preview(self, count: int) -> dict:
        if not isinstance(count, int) or not 1 <= count <= core.MAX_COUNT:
            raise ServiceError(f"Enter a number between 1 and {core.MAX_COUNT}")
        existing = self.existing_users()
        try:
            emails = core.next_sequence(existing, count)
        except core.FatalError as e:
            raise ServiceError(str(e)) from None
        numbers = core.existing_sequence_numbers(existing)
        plan_id = secrets.token_urlsafe(16)
        now_ts = time.time()
        self._plans = {k: v for k, v in self._plans.items() if v["expires"] > now_ts}
        self._plans[plan_id] = {"count": count, "emails": emails, "expires": now_ts + PLAN_TTL}
        return {
            "plan_id": plan_id, "emails": emails,
            "highest_existing": f"{numbers[-1]:0{core.SEQUENCE_WIDTH}d}@{core.DOMAIN}" if numbers else None,
            "settings": {"passwordReset": True, "searchIndexing": True, "welcomeEmail": False,
                         "recoveryEmail": None},
            "expires_in": PLAN_TTL,
        }

    def start_batch(self, plan_id: str, key: bytes) -> str:
        plan = self._plans.pop(plan_id, None)
        if not plan or plan["expires"] < time.time():
            raise ServiceError("This preview has expired. Click Preview again.", 409)
        if not self._batch_lock.acquire(blocking=False):
            raise ServiceError("Another batch is already running.", 409)
        try:
            fresh = core.next_sequence(self.existing_users(), plan["count"])
            if fresh != plan["emails"]:
                raise ServiceError("Mailboxes changed since the preview. Click Preview again.", 409)
        except core.FatalError as e:
            self._batch_lock.release()
            raise ServiceError(str(e)) from None
        except Exception:
            self._batch_lock.release()
            raise

        job_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        job = {
            "id": job_id, "state": "running", "started_at": now(), "finished_at": None,
            "items": [{"email": e, "status": "queued", "detail": ""} for e in plan["emails"]],
            "counts": {}, "excel": {"state": "pending", "message": "Waiting for first mailbox"},
            "excel_rows": 0,
        }
        self.jobs[job_id] = job
        self.store.save_batch(job_id, job["started_at"], None, self._job_summary(job))
        self.store.audit("batch_start", "ok", detail=f"{job_id}: {len(plan['emails'])} mailbox(es)")
        threading.Thread(target=self._run_batch, args=(job, key), daemon=True).start()
        return job_id

    @staticmethod
    def _job_summary(job: dict) -> dict:
        counts = {}
        for it in job["items"]:
            counts[it["status"]] = counts.get(it["status"], 0) + 1
        job["counts"] = counts
        return {"state": job["state"], "counts": counts, "excel": job["excel"], "excel_rows": job["excel_rows"]}

    def _set_item(self, job: dict, item: dict, status: str, detail: str = "") -> None:
        item["status"], item["detail"] = status, detail
        self.store.save_batch_item(job["id"], item["email"], status, detail)
        log.info("batch %s: %s %s %s", job["id"], item["email"], status, detail)

    def _run_batch(self, job: dict, key: bytes) -> None:
        csvlog = core.CsvLog(self.config.credentials_csv)
        errors_in_row = 0
        excel_live = True
        try:
            for idx, item in enumerate(job["items"]):
                email = item["email"]
                if errors_in_row >= core.MAX_CONSECUTIVE_ERRORS:
                    for rest in job["items"][idx:]:
                        self._set_item(job, rest, "not_attempted", "stopped after repeated errors")
                    break
                self._set_item(job, item, "working")

                try:
                    details = self._api("get_user", email)
                except (core.ApiError, ServiceError) as e:
                    self._set_item(job, item, "failed", f"existence check inconclusive: {e}; not created")
                    errors_in_row += 1
                    continue
                except core.FatalError as e:
                    self._set_item(job, item, "failed", str(e))
                    errors_in_row = core.MAX_CONSECUTIVE_ERRORS
                    continue
                if details is not None:
                    self._set_item(job, item, "skipped", "already exists on Purelymail")
                    continue

                password = core.generate_password()
                self.store.save_mailbox(key, email, "created", "pending", password)
                csvlog.write(email, "pending", password)
                try:
                    self._api("create_user", email, password)
                except core.ApiError as e:
                    msg = _scrub(str(e), password)
                    if e.ambiguous:
                        self.store.set_mailbox_status(email, "unknown")
                        csvlog.write(email, "unknown", password)
                        self._set_item(job, item, "unknown",
                                       f"{msg}. Check the Purelymail dashboard; not retried.")
                    else:
                        self.store.set_mailbox_status(email, "failed")
                        csvlog.write(email, "error", password)
                        self._set_item(job, item, "failed", msg)
                    self.store.audit("create", item["status"], email, msg)
                    errors_in_row += 1
                    continue
                except (core.FatalError, ServiceError) as e:
                    self.store.set_mailbox_status(email, "failed")
                    csvlog.write(email, "error", password)
                    self._set_item(job, item, "failed", _scrub(str(e), password))
                    errors_in_row = core.MAX_CONSECUTIVE_ERRORS
                    continue

                errors_in_row = 0
                self.store.set_mailbox_status(email, "active")
                csvlog.write(email, "created", password)
                self.store.audit("create", "created", email)
                self.store.queue_excel(email, "create", dt.date.today().isoformat())
                note = ""
                try:
                    d = self._api("get_user", email) or {}
                    if d.get("enableSpamFiltering") is not True:
                        note = "spam filtering is off; enable it in the Purelymail dashboard"
                except Exception:  # verification is best-effort
                    note = "created; settings check failed"
                self._set_item(job, item, "created", note)

                if excel_live:
                    res = self.sync_excel(key)
                    job["excel_rows"] += res.get("written", 0)
                    if res["state"] != "ok":
                        excel_live = False  # retry once at the end instead of after every mailbox
                    job["excel"] = res
        except Exception as e:  # never leave a job stuck in "running"
            log.exception("batch %s crashed", job["id"])
            for it in job["items"]:
                if it["status"] in ("queued", "working"):
                    self._set_item(job, it, "not_attempted", f"internal error: {type(e).__name__}")
        finally:
            csvlog.close()
            if self.store.pending_excel():
                res = self.sync_excel(key)
                job["excel_rows"] += res.get("written", 0)
                job["excel"] = res
            elif job["excel"]["state"] == "pending":
                job["excel"] = {"state": "ok", "message": "No changes needed", "pending": 0}
            created = sum(1 for it in job["items"] if it["status"] == "created")
            x = job["excel"]
            if x["state"] == "ok" and created:
                job["excel"] = {**x, "message": f"Updated successfully: {created} new mailbox(es) recorded "
                                                f"({job['excel_rows']} row change(s))"}
            elif x["state"] != "ok":
                job["excel"] = {**x, "message": f"NOT updated: {x['message']} "
                                                f"({x.get('pending', 0)} change(s) waiting; use Retry Excel update)"}
            job["state"] = "done"
            job["finished_at"] = now()
            self.store.save_batch(job["id"], job["started_at"], job["finished_at"], self._job_summary(job))
            self.store.audit("batch_done", "ok", detail=f"{job['id']}: {job['counts']}")
            self._batch_lock.release()

    def job(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job:
            self._job_summary(job)
            return job
        saved = self.store.get_batch(job_id)
        if not saved:
            raise ServiceError("Unknown batch", 404)
        saved["items"] = self.store.batch_items(job_id)
        return saved

    def export_batch_csv(self, job_id: str, key: bytes) -> str:
        if not self.store.get_batch(job_id):
            raise ServiceError("Unknown batch", 404)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["email", "password", "status", "created_at"])
        for it in self.store.batch_items(job_id):
            if it["status"] != "created":
                continue
            w.writerow([it["email"], self.store.get_password(key, it["email"]) or "", "created", it["at"]])
        self.store.audit("export_batch", "ok", detail=job_id)
        return out.getvalue()

    # ------------------------------------------------------------------ password reset

    def reset_password(self, email: str, key: bytes) -> dict:
        email = email.strip().lower()
        m = self.store.get_mailbox(email)
        if (email in core.PROTECTED or email in core.EXCLUDE_FROM_SEQUENCE or not m
                or m["status"] != "active" or m["source"] not in ("created", "imported")):
            self.store.audit("reset", "refused", email, "not managed by this tool")
            raise ServiceError("Password reset is only allowed for mailboxes created or imported by this tool.", 403)
        try:
            if self._api("get_user", email) is None:
                raise ServiceError(f"{email} no longer exists on Purelymail.", 404)
        except core.ApiError as e:
            raise ServiceError(f"Could not check {email} on Purelymail: {e}", 502) from None

        password = core.generate_password()
        csvlog = core.CsvLog(self.config.credentials_csv)
        try:
            try:
                self._api("reset_password", email, password)
            except core.ApiError as e:
                msg = _scrub(str(e), password)
                if e.ambiguous:
                    csvlog.write(email, "reset_unknown", password)
                    self.store.audit("reset", "unknown", email, msg)
                    return {"ok": False, "unknown": True, "password": password,
                            "message": f"Purelymail did not confirm the reset ({msg}). Either the old password "
                                       "or this new one is now active; try logging in, or reset again. "
                                       "The tracking workbook was not changed."}
                self.store.audit("reset", "failed", email, msg)
                raise ServiceError(f"Purelymail rejected the reset: {msg}", 502) from None
            self.store.set_mailbox_password(key, email, password)
            csvlog.write(email, "reset", password)
        finally:
            csvlog.close()
        self.store.audit("reset", "ok", email)
        self.store.queue_excel(email, "reset", None)
        return {"ok": True, "password": password, "excel": self.sync_excel(key)}

    # ------------------------------------------------------------------ one-time import from credentials.csv

    @staticmethod
    def _csv_created_rows(path: Path) -> dict[str, tuple[str, str]]:
        """Current password per created email: {email: (password, created_at)}.

        A later 'reset' row (written by this app) replaces the password but keeps the creation time.
        """
        rows: dict[str, tuple[str, str]] = {}
        try:
            with open(path, newline="") as fh:
                for r in csv.DictReader(fh):
                    email = (r.get("email") or "").strip().lower()
                    if not email or not r.get("password"):
                        continue
                    if r.get("status") == "created":
                        rows[email] = (r["password"], r.get("created_at") or "")
                    elif r.get("status") == "reset" and email in rows:
                        rows[email] = (r["password"], rows[email][1])
        except OSError:
            return {}
        return rows

    def import_from_csv(self, key: bytes) -> dict:
        path = self.config.credentials_csv
        if not path.exists():
            raise ServiceError(f"{path} not found", 404)
        rows = self._csv_created_rows(path)
        existing = self.existing_users()
        imported, skipped = [], []
        for email, (password, created_at) in sorted(rows.items()):
            if core.validate(email) or email in core.EXCLUDE_FROM_SEQUENCE:
                skipped.append((email, "protected or invalid address"))
            elif email not in existing:
                skipped.append((email, "not found on Purelymail"))
            elif self.store.get_mailbox(email):
                skipped.append((email, "already managed"))
            else:
                self.store.save_mailbox(key, email, "imported", "active", password, created_at or None)
                self.store.queue_excel(email, "import", _local_date(created_at))
                imported.append(email)
                self.store.audit("import", "ok", email)
        if imported or rows:
            self.store.set_setting("csv_import_done", now())
        return {"imported": imported, "skipped": skipped, "excel": self.sync_excel(key)}

    # ------------------------------------------------------------------ tracking workbook

    def set_tracking_path(self, path_str: str) -> dict:
        path_str = (path_str or "").strip().strip('"').strip("'")
        if not path_str:
            raise ServiceError("Paste the full path of the workbook first, e.g. /Users/you/BGYHub/BGYHub邮箱使用记录.xlsx")
        path = Path(path_str).expanduser()
        info = tracking_xlsx.describe(path)
        if not info["valid"]:
            raise ServiceError(f"Not a usable tracking workbook: {info['reason']}")
        self.store.set_setting("tracking_path", str(path.resolve()))
        self.store.audit("tracking_path", "ok", detail=str(path.resolve()))
        info.pop("emails", None)
        return info

    def find_tracking_candidates(self) -> dict:
        blocked: list[str] = []
        found = tracking_xlsx.find_candidates([Path(d) for d in self.config.search_dirs], blocked)
        return {"candidates": found, "blocked": sorted(set(blocked)),
                "blocked_hint": tracking_xlsx.PRIVACY_HINT if blocked else ""}

    def dismiss_pending(self, pending_id: int) -> None:
        self.store.clear_excel([pending_id])
        self.store.audit("excel_dismiss", "ok", detail=str(pending_id))

    def sync_excel(self, key: bytes) -> dict:
        """Write all queued rows to the workbook in one safe update."""
        with self._excel_lock:
            pending = self.store.pending_excel()
            if not pending:
                return {"state": "ok", "message": "Tracking workbook is up to date", "pending": 0, "written": 0}
            path = self.tracking_path()
            if not path:
                return {"state": "not_configured", "pending": len(pending), "written": 0,
                        "message": "No tracking workbook selected yet. Choose it in Settings, then Retry."}
            ops, used = [], []
            for p in pending:
                password = self.store.get_password(key, p["email"])
                if password is None:
                    self.store.clear_excel([p["id"]])
                    continue
                date = dt.date.fromisoformat(p["date"]) if p["date"] else None
                ops.append(tracking_xlsx.RowOp(p["kind"], p["email"], password, date))
                used.append(p)
            try:
                res = tracking_xlsx.apply(path, ops, self.config.backup_dir, self.config.lock_path)
            except tracking_xlsx.TrackingError as e:
                self.store.audit("excel_update", "failed", detail=str(e))
                log.warning("Excel update failed: %s", e)
                return {"state": "failed", "message": str(e), "pending": len(pending), "written": 0}
            conflicted = {e for e, _ in res.conflicts}
            self.store.clear_excel([p["id"] for p in used if p["email"] not in conflicted])
            written = len(res.added) + len(res.updated)
            self.store.audit("excel_update", "ok" if not conflicted else "partial",
                             detail=f"added={res.added} updated={res.updated} conflicts={res.conflicts} "
                                    f"backup={res.backup.name if res.backup else None}")
            state = "conflict" if conflicted else "ok"
            msg = (f"Tracking workbook updated ({len(res.added)} added, {len(res.updated)} updated)"
                   if written or res.password_column_added else "Tracking workbook already up to date")
            if conflicted:
                msg += ". Needs attention: " + "; ".join(f"{e}: {why}" for e, why in res.conflicts)
            return {"state": state, "message": msg, "pending": len(self.store.pending_excel()),
                    "written": written, "added": res.added, "updated": res.updated,
                    "conflicts": res.conflicts, "password_column_added": res.password_column_added,
                    "backup": str(res.backup) if res.backup else None, "path": str(path)}
