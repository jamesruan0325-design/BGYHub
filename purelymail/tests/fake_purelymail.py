"""In-process fake of the four Purelymail endpoints the tools use. Test-only."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "test-token-SECRET"


class FakePurelymail:
    def __init__(self, users: set[str]):
        self.users = set(users)
        self.passwords: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []
        self.fail_create: dict[str, int] = {}  # local part -> HTTP status to return
        self.fail_modify: dict[str, int] = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                ep = self.path.rsplit("/", 1)[-1]
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                fake.calls.append((ep, body))
                if self.headers.get("Purelymail-Api-Token") != TOKEN:
                    return self._send(401, {"type": "error", "message": "bad token"})
                if ep == "listUser":
                    return self._send(200, {"type": "success", "result": {"users": sorted(fake.users)}})
                if ep == "getUser":
                    if body["userName"] in fake.users:
                        return self._send(200, {"type": "success", "result": {
                            "enableSearchIndexing": True, "recoveryEnabled": True,
                            "requireTwoFactorAuthentication": False, "enableSpamFiltering": True,
                            "resetMethods": []}})
                    return self._send(200, {"type": "error", "code": "userNotFound", "message": "User not found"})
                if ep == "createUser":
                    local = body["userName"]
                    if local in fake.fail_create:
                        code = fake.fail_create[local]
                        return self._send(code, {"type": "error", "code": "invalid", "message": "rejected"}
                                          if code < 500 else {})
                    email = f"{local}@{body['domainName']}"
                    if email in fake.users:
                        return self._send(200, {"type": "error", "code": "exists", "message": "User exists"})
                    fake.users.add(email)
                    fake.passwords[email] = body["password"]
                    return self._send(200, {"type": "success", "result": {}})
                if ep == "modifyUser":
                    email = body["userName"]
                    if email.split("@")[0] in fake.fail_modify:
                        return self._send(fake.fail_modify[email.split("@")[0]], {})
                    if set(body) - {"userName", "newPassword"}:
                        return self._send(400, {"type": "error", "message": "unexpected fields"})
                    fake.passwords[email] = body["newPassword"]
                    return self._send(200, {"type": "success", "result": {}})
                return self._send(400, {"type": "error", "message": f"unexpected endpoint {ep}"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def endpoints_called(self) -> set[str]:
        return {c[0] for c in self.calls}
