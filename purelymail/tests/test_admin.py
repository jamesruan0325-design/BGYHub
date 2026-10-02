"""End-to-end tests of the admin app against a fake Purelymail server."""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openpyxl  # noqa: E402

import mailbox_core  # noqa: E402
from tests.fake_purelymail import TOKEN, FakePurelymail  # noqa: E402
from tests.test_tracking_xlsx import make_workbook  # noqa: E402

BASE = "http://127.0.0.1:8787"
ADMIN_PW = "correct horse battery staple"
EXISTING = {"service@bgyhub.com", "123@bgyhub.com", "other@bgyhub.com",
            "001@bgyhub.com", "002@bgyhub.com", "003@bgyhub.com", "x@elsewhere.com"}


class AdminAppTests(unittest.TestCase):
    def setUp(self):
        mailbox_core.MIN_REQUEST_INTERVAL = 0
        mailbox_core.RETRY_DELAYS = [0, 0]
        os.environ[mailbox_core.TOKEN_ENV] = TOKEN
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.fake = FakePurelymail(EXISTING).__enter__()
        self.workbook = self.dir / "Documents" / "BGYHub邮箱使用记录.xlsx"
        self.workbook.parent.mkdir()
        make_workbook(self.workbook)
        out = self.dir / "output"
        out.mkdir()
        with open(out / "credentials.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["email", "password", "status", "created_at"])
            for i, ch in ((1, "a"), (2, "b"), (3, "c")):
                w.writerow([f"00{i}@bgyhub.com", f"P{i}" + ch * 22, "pending", "2026-09-30T21:19:00Z"])
                w.writerow([f"00{i}@bgyhub.com", f"P{i}" + ch * 22, "created", "2026-09-30T21:19:01Z"])
        self.log_stream = io.StringIO()
        handler = logging.StreamHandler(self.log_stream)
        for name in ("purelymail", "purelymail.admin"):
            logging.getLogger(name).addHandler(handler)
            logging.getLogger(name).setLevel(logging.DEBUG)
        self.log_handler = handler

        from admin.app import create_app
        self.app = create_app(data_dir=self.dir / "data", output_dir=out, base_url=self.fake.base_url,
                              search_dirs=[self.dir / "Documents"])
        self.client = self.app.test_client()
        self.responses: list[bytes] = []

    def tearDown(self):
        for name in ("purelymail", "purelymail.admin"):
            logging.getLogger(name).removeHandler(self.log_handler)
        self.fake.__exit__(None, None, None)
        self.tmp.cleanup()

    # ------------------------------------------------------------------ helpers

    def csrf(self) -> str:
        with self.client.session_transaction(base_url=BASE) as s:
            return s.get("csrf", "")

    def get(self, path, **kw):
        r = self.client.get(path, base_url=BASE, **kw)
        self.responses.append(r.data)
        return r

    def post(self, path, json=None, csrf=True, **kw):
        headers = kw.pop("headers", {})
        if csrf:
            headers["X-CSRF-Token"] = self.csrf()
        r = self.client.post(path, base_url=BASE, json=json, headers=headers, **kw)
        self.responses.append(r.data)
        return r

    def setup_admin(self):
        page = self.get("/setup").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        r = self.client.post("/setup", base_url=BASE, data={
            "csrf_token": token, "password": ADMIN_PW, "password2": ADMIN_PW})
        self.assertEqual(r.status_code, 302)
        self.get("/")  # issues the page CSRF token

    def select_workbook_and_import(self):
        r = self.get("/api/tracking/candidates")
        self.assertEqual([Path(c["path"]).name for c in r.json["candidates"]], [self.workbook.name])
        self.assertEqual(self.post("/api/tracking/select", {"path": str(self.workbook)}).status_code, 200)
        r = self.post("/api/import-csv")
        self.assertEqual(r.status_code, 200, r.json)
        return r.json

    def run_batch(self, count):
        prev = self.post("/api/preview", {"count": count})
        self.assertEqual(prev.status_code, 200, prev.json)
        r = self.post("/api/create", {"plan_id": prev.json["plan_id"]})
        self.assertEqual(r.status_code, 200, r.json)
        job_id = r.json["job_id"]
        for _ in range(200):
            job = self.get(f"/api/jobs/{job_id}").json
            if job["state"] == "done":
                return prev.json, job
            time.sleep(0.05)
        self.fail("batch did not finish")

    def workbook_rows(self) -> dict:
        ws = openpyxl.load_workbook(self.workbook).active
        return {r[0]: r for r in ws.iter_rows(min_row=2, values_only=True)}

    def assert_no_secrets_leaked(self):
        everything = b"".join(self.responses)
        self.assertNotIn(TOKEN.encode(), everything)
        logs = self.log_stream.getvalue()
        self.assertNotIn(TOKEN, logs)
        for pw in self.fake.passwords.values():
            self.assertNotIn(pw, logs)
        for f in (self.dir / "output").glob("*.log"):
            text = f.read_text()
            self.assertNotIn(TOKEN, text)
            for pw in self.fake.passwords.values():
                self.assertNotIn(pw, text)

    # ------------------------------------------------------------------ security

    def test_guards(self):
        self.assertEqual(self.client.get("/healthz", base_url="http://evil.example:8787").status_code, 400)
        self.assertEqual(self.get("/api/status").status_code, 401)
        self.setup_admin()
        self.assertEqual(self.get("/api/status").status_code, 200)
        self.assertEqual(self.post("/api/preview", {"count": 1}, csrf=False).status_code, 403)
        r = self.post("/api/preview", {"count": 1}, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.get("/api/tracking/download").status_code, 428)  # needs re-auth
        r = self.get("/")
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertEqual(self.post("/api/lock").status_code, 200)
        self.assertEqual(self.get("/api/status").status_code, 401)

    def test_wrong_login_password(self):
        self.setup_admin()
        self.post("/api/lock")
        page = self.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        r = self.client.post("/login", base_url=BASE, data={"csrf_token": token, "password": "nope"})
        self.assertIn(b"Wrong password", r.data)

    # ------------------------------------------------------------------ main flow

    def test_import_create_export_reset_flow(self):
        self.setup_admin()
        imp = self.select_workbook_and_import()
        self.assertEqual(imp["imported"], ["001@bgyhub.com", "002@bgyhub.com", "003@bgyhub.com"])
        self.assertEqual(imp["excel"]["state"], "ok")
        rows = self.workbook_rows()
        self.assertEqual(rows["002@bgyhub.com"][6], "P2" + "b" * 22)
        self.assertEqual(rows["002@bgyhub.com"][1], "未使用")
        self.assertEqual(rows["002@bgyhub.com"][5], "Purelymail 创建")
        self.assertEqual(self.get("/api/status").json["csv_import"]["available"], False)

        preview, job = self.run_batch(3)
        self.assertEqual(preview["emails"], ["004@bgyhub.com", "005@bgyhub.com", "006@bgyhub.com"])
        self.assertEqual(preview["highest_existing"], "003@bgyhub.com")
        self.assertEqual(job["counts"], {"created": 3})
        self.assertEqual(job["excel"]["state"], "ok")
        rows = self.workbook_rows()
        for e in preview["emails"]:
            self.assertEqual(rows[e][6], self.fake.passwords[e])  # exact password Purelymail accepted
            self.assertEqual(rows[e][1], "未使用")
        self.assertEqual(len(rows), 2 + 3 + 3)  # service, 123, 001-003, 004-006
        self.assertEqual(len(set(self.fake.passwords.values())), 3)

        bodies = [b for ep, b in self.fake.calls if ep == "createUser"]
        for b in bodies:
            self.assertEqual((b["enablePasswordReset"], b["enableSearchIndexing"], b["sendWelcomeEmail"]),
                             (True, True, False))
            self.assertNotIn("recoveryEmail", b)
            self.assertEqual(len(b["password"]), 24)

        # Export needs re-auth, then contains the created rows with their passwords.
        self.assertEqual(self.get(f"/api/jobs/{job['id']}/export.csv").status_code, 428)
        self.assertEqual(self.post("/api/reauth", {"password": "wrong"}).status_code, 403)
        self.assertEqual(self.post("/api/reauth", {"password": ADMIN_PW}).status_code, 200)
        exp = self.get(f"/api/jobs/{job['id']}/export.csv")
        got = {r["email"]: r["password"] for r in csv.DictReader(io.StringIO(exp.get_data(as_text=True)))}
        self.assertEqual(got, {e: self.fake.passwords[e] for e in preview["emails"]})
        dl = self.get("/api/tracking/download")
        self.assertEqual(dl.status_code, 200)
        dl.close()

        # Reset a managed mailbox: Purelymail and the workbook get the same new password.
        before = self.workbook_rows()["005@bgyhub.com"]
        r = self.post("/api/mailboxes/005@bgyhub.com/reset-password", {"confirm": "005@bgyhub.com"})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json["password"], self.fake.passwords["005@bgyhub.com"])
        after = self.workbook_rows()["005@bgyhub.com"]
        self.assertEqual(after[:6], before[:6])
        self.assertEqual(after[6], r.json["password"])
        modify = [b for ep, b in self.fake.calls if ep == "modifyUser"]
        self.assertEqual(modify, [{"userName": "005@bgyhub.com", "newPassword": r.json["password"]}])
        # Imported mailboxes can be reset too.
        self.assertEqual(self.post("/api/mailboxes/001@bgyhub.com/reset-password",
                                   {"confirm": "001@bgyhub.com"}).status_code, 200)

        self.assertTrue(self.fake.endpoints_called() <= {"listUser", "getUser", "createUser", "modifyUser"})
        self.assert_no_secrets_leaked()

        # The database never holds plaintext passwords.
        raw = (self.dir / "data" / "admin.db").read_bytes()
        for pw in self.fake.passwords.values():
            self.assertNotIn(pw.encode(), raw)

    def test_reset_refused_for_unmanaged_mailboxes(self):
        self.setup_admin()
        self.select_workbook_and_import()
        for email in ("service@bgyhub.com", "123@bgyhub.com", "other@bgyhub.com"):
            r = self.post(f"/api/mailboxes/{email}/reset-password", {"confirm": email})
            self.assertEqual(r.status_code, 403, email)
        r = self.post("/api/mailboxes/001@bgyhub.com/reset-password", {"confirm": "002@bgyhub.com"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(any(ep == "modifyUser" for ep, _ in self.fake.calls))
        rows = self.get("/api/mailboxes").json["mailboxes"]
        can_reset = {m["email"] for m in rows if m["can_reset"]}
        self.assertEqual(can_reset, {"001@bgyhub.com", "002@bgyhub.com", "003@bgyhub.com"})
        self.assertNotIn("x@elsewhere.com", {m["email"] for m in rows})

    def test_failures_are_not_written_to_workbook(self):
        self.setup_admin()
        self.select_workbook_and_import()
        self.fake.fail_create["005"] = 400   # rejected
        self.fake.fail_create["006"] = 500   # ambiguous
        _, job = self.run_batch(3)
        status = {it["email"]: it["status"] for it in job["items"]}
        self.assertEqual(status, {"004@bgyhub.com": "created", "005@bgyhub.com": "failed",
                                  "006@bgyhub.com": "unknown"})
        self.assertEqual(job["counts"], {"created": 1, "failed": 1, "unknown": 1})
        rows = self.workbook_rows()
        self.assertIn("004@bgyhub.com", rows)
        self.assertNotIn("005@bgyhub.com", rows)
        self.assertNotIn("006@bgyhub.com", rows)
        creates = [b["userName"] for ep, b in self.fake.calls if ep == "createUser"]
        self.assertEqual(creates.count("006"), 1)  # ambiguous create never retried
        self.assert_no_secrets_leaked()

    def test_excel_open_queues_updates_until_retry(self):
        self.setup_admin()
        self.select_workbook_and_import()
        lock = self.workbook.with_name("~$" + self.workbook.name)
        lock.write_text("x")
        before = self.workbook.read_bytes()
        _, job = self.run_batch(2)
        self.assertEqual(job["counts"], {"created": 2})
        self.assertEqual(job["excel"]["state"], "failed")
        self.assertIn("NOT updated", job["excel"]["message"])
        self.assertEqual(self.workbook.read_bytes(), before)
        self.assertEqual(self.get("/api/status").json["excel_pending"], 2)
        lock.unlink()
        r = self.post("/api/excel/sync")
        self.assertEqual(r.json["state"], "ok")
        rows = self.workbook_rows()
        self.assertEqual(rows["005@bgyhub.com"][6], self.fake.passwords["005@bgyhub.com"])
        self.assertEqual(self.get("/api/status").json["excel_pending"], 0)

    def test_select_workbook_privacy_block_is_a_clear_400(self):
        from unittest import mock
        import tracking_xlsx
        self.setup_admin()
        with mock.patch.object(tracking_xlsx.Path, "exists", side_effect=PermissionError(1, "Operation not permitted")):
            r = self.post("/api/tracking/select", {"path": str(self.workbook)})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Privacy", r.json["error"])

    def test_preview_rules(self):
        self.setup_admin()
        self.assertEqual(self.post("/api/preview", {"count": 0}).status_code, 400)
        self.assertEqual(self.post("/api/preview", {"count": 51}).status_code, 400)
        prev = self.post("/api/preview", {"count": 2}).json
        self.fake.users.add("004@bgyhub.com")  # someone created one meanwhile
        r = self.post("/api/create", {"plan_id": prev["plan_id"]})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.post("/api/create", {"plan_id": prev["plan_id"]}).status_code, 409)  # single use
        self.assertFalse(any(ep == "createUser" for ep, _ in self.fake.calls))

    def test_batch_without_workbook_keeps_changes_pending(self):
        self.setup_admin()
        _, job = self.run_batch(1)
        self.assertEqual(job["excel"]["state"], "not_configured")
        self.assertEqual(self.post("/api/tracking/select", {"path": str(self.workbook)}).status_code, 200)
        self.assertEqual(self.post("/api/excel/sync").json["state"], "ok")
        self.assertIn("004@bgyhub.com", self.workbook_rows())


if __name__ == "__main__":
    unittest.main()
