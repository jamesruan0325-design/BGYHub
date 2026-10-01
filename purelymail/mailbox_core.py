"""Shared Purelymail logic for the CLI (create_users.py) and the local admin app.

Only four endpoints are ever called: listUser and getUser (read-only),
createUser, and modifyUser (password reset of tool-managed mailboxes only).
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import secrets
import ssl
import string
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DOMAIN = "bgyhub.com"
API_BASE = "https://purelymail.com"
TOKEN_ENV = "PURELYMAIL_API_TOKEN"

# Addresses this script must never touch, even if they appear in the input.
PROTECTED = {"service@bgyhub.com"}

# Numbered mailboxes (--count mode): NNN@bgyhub.com, zero-padded.
SEQUENCE_WIDTH = 3
SEQUENCE_RE = re.compile(r"^(\d{%d})@%s$" % (SEQUENCE_WIDTH, re.escape(DOMAIN)))
# Existing numeric addresses that are not part of the 001, 002, ... sequence.
# They are ignored when finding the highest number, and skipped if reached.
EXCLUDE_FROM_SEQUENCE = {"123@bgyhub.com"}
MAX_COUNT = 50  # per run

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_USERS_FILE = SCRIPT_DIR / "users.txt"
ENV_FILE = SCRIPT_DIR / ".env"
OUTPUT_DIR = SCRIPT_DIR / "output"
CSV_FIELDS = ["email", "password", "status", "created_at"]

MIN_REQUEST_INTERVAL = 2.0  # seconds between any two API requests
RETRY_DELAYS = [5, 15]  # at most two retries, only for safe-to-retry cases
REQUEST_TIMEOUT = 30
MAX_CONSECUTIVE_ERRORS = 3

PASSWORD_LENGTH = 24
# No comma, quotes, backslash or spaces: keeps the CSV and shells simple.
PASSWORD_SYMBOLS = "!@#$%^&*-_=+?"

EMAIL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?@" + re.escape(DOMAIN) + "$")
NOT_FOUND_HINTS = ("not found", "notfound", "does not exist", "doesn't exist", "no such", "unknown user")

log = logging.getLogger("purelymail")


class ApiError(Exception):
    """Purelymail returned an error, or the request failed."""

    def __init__(self, message: str, *, code: str = "", ambiguous: bool = False):
        super().__init__(message)
        self.code = code
        # True when we cannot tell whether the server applied the request.
        self.ambiguous = ambiguous


class FatalError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- config


def load_token() -> str:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip().removeprefix("export ").strip() == TOKEN_ENV:
                token = value.strip().strip('"').strip("'")
                break
    if not token:
        raise FatalError(
            f"No API token. Set {TOKEN_ENV} in your environment or in {ENV_FILE} "
            f"(copy .env.example to .env)."
        )
    return token


def load_emails(path: Path) -> list[str]:
    emails = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if line and line not in emails:
            emails.append(line)
    return emails


# --------------------------------------------------------------------------- passwords


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    alphanumeric = string.ascii_letters + string.digits
    alphabet = alphanumeric + PASSWORD_SYMBOLS
    while True:
        # First character alphanumeric so spreadsheet apps never read it as a formula.
        pw = secrets.choice(alphanumeric) + "".join(secrets.choice(alphabet) for _ in range(length - 1))
        if (
            any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw)
            and any(c in PASSWORD_SYMBOLS for c in pw)
        ):
            return pw


# --------------------------------------------------------------------------- API client


class PurelymailClient:
    def __init__(self, token: str, base_url: str = API_BASE):
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._last_request = 0.0
        self._ssl = self._ssl_context()

    @staticmethod
    def _ssl_context() -> ssl.SSLContext:
        try:
            import certifi  # optional; helps python.org builds on macOS

            return ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            return ssl.create_default_context()

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    def _throttle(self) -> None:
        wait = self._last_request + MIN_REQUEST_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _post_once(self, endpoint: str, body: dict) -> dict:
        self._throttle()
        req = urllib.request.Request(
            f"{self._base_url}/api/v0/{endpoint}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Purelymail-Api-Token": self._token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=self._ssl) as resp:
                raw = resp.read().decode("utf-8", "replace")
                status = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            status = e.code
        except ssl.SSLError as e:
            raise FatalError(
                f"TLS error talking to Purelymail: {e}. On macOS with python.org Python, run "
                "'Install Certificates.command' from your Python folder, or 'pip3 install certifi'."
            ) from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLError):
                raise FatalError(
                    f"TLS error talking to Purelymail: {e.reason}. On macOS with python.org Python, run "
                    "'Install Certificates.command' from your Python folder, or 'pip3 install certifi'."
                ) from None
            refused = isinstance(e.reason, ConnectionRefusedError)
            raise ApiError(self._redact(f"network error: {e.reason}"), code="network",
                           ambiguous=not refused) from None
        except (TimeoutError, OSError) as e:
            raise ApiError(self._redact(f"network error: {e}"), code="network", ambiguous=True) from None

        log.debug("%s -> HTTP %s", endpoint, status)
        if status == 429:
            raise ApiError("HTTP 429 rate limited", code="http_429")
        if status == 401 or status == 403:
            raise FatalError(f"HTTP {status} from Purelymail: API token rejected or not permitted.")
        if status >= 500:
            raise ApiError(f"HTTP {status} server error", code=f"http_{status}", ambiguous=True)

        try:
            payload = json.loads(raw)
        except ValueError:
            raise ApiError(f"HTTP {status}: response was not JSON", code=f"http_{status}",
                           ambiguous=status < 400) from None

        if payload.get("type") == "error" or status >= 400:
            code = str(payload.get("code", f"http_{status}"))
            message = self._redact(str(payload.get("message") or payload.get("error") or raw[:200]))
            raise ApiError(f"{code}: {message}", code=code)
        return payload.get("result", payload)

    def _post(self, endpoint: str, body: dict, *, idempotent: bool) -> dict:
        """POST with limited retries.

        Read-only calls retry on 429, 5xx and network errors. createUser only
        retries when the server certainly did not act (429 / connection refused);
        anything ambiguous is surfaced immediately so we never double-create.
        """
        attempt = 0
        while True:
            try:
                return self._post_once(endpoint, body)
            except ApiError as e:
                retryable = e.code == "http_429" or e.code.startswith("http_5") or e.code == "network"
                if not idempotent and e.ambiguous:
                    retryable = False
                if not retryable or attempt >= len(RETRY_DELAYS):
                    raise
                delay = RETRY_DELAYS[attempt]
                attempt += 1
                log.warning("%s failed (%s); retry %d/%d in %ds", endpoint, e, attempt, len(RETRY_DELAYS), delay)
                time.sleep(delay)

    # Only these three endpoints are ever called.

    def list_users(self) -> set[str]:
        result = self._post("listUser", {}, idempotent=True)
        return {u.lower() for u in result.get("users", [])}

    def get_user(self, email: str) -> dict | None:
        """Return user details, or None if Purelymail says the user does not exist."""
        try:
            return self._post("getUser", {"userName": email}, idempotent=True)
        except ApiError as e:
            if not e.ambiguous and any(h in str(e).lower() for h in NOT_FOUND_HINTS):
                return None
            raise

    def reset_password(self, email: str, new_password: str) -> None:
        """Set a new password. Only ever sends userName + newPassword.

        Retrying is safe: a repeat sends the same new password again.
        """
        self._post("modifyUser", {"userName": email, "newPassword": new_password}, idempotent=True)

    def create_user(self, email: str, password: str) -> None:
        local, domain = email.split("@", 1)
        self._post(
            "createUser",
            {
                "userName": local,
                "domainName": domain,
                "password": password,
                "enablePasswordReset": True,
                "enableSearchIndexing": True,
                "sendWelcomeEmail": False,
            },
            idempotent=False,
        )


# --------------------------------------------------------------------------- output


class CsvLog:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        new = not path.exists()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(path, 0o600)
        self._fh = os.fdopen(fd, "a", newline="")
        self._writer = csv.DictWriter(self._fh, fieldnames=CSV_FIELDS)
        if new:
            self._writer.writeheader()
            self._flush()

    def _flush(self) -> None:
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def write(self, email: str, status: str, password: str = "") -> None:
        self._writer.writerow({"email": email, "password": password, "status": status, "created_at": now_iso()})
        self._flush()

    def close(self) -> None:
        self._fh.close()




def validate(email: str) -> str | None:
    """Return a refusal status, or None if the address may be processed."""
    if email in PROTECTED:
        return "refused_protected"
    if not EMAIL_RE.match(email):
        return "refused_invalid"
    return None


def existing_sequence_numbers(existing: set[str]) -> list[int]:
    """Sorted numbers of existing NNN@bgyhub.com mailboxes, ignoring exclusions."""
    return sorted(
        int(m.group(1))
        for e in existing - EXCLUDE_FROM_SEQUENCE
        if (m := SEQUENCE_RE.match(e))
    )


def next_sequence(existing: set[str], count: int) -> list[str]:
    """Next `count` numbered addresses after the highest existing one.

    Skips any address that already exists or is protected/excluded; never fills gaps.
    """
    if not 1 <= count <= MAX_COUNT:
        raise FatalError(f"count must be between 1 and {MAX_COUNT}")
    numbers = existing_sequence_numbers(existing)
    limit = 10 ** SEQUENCE_WIDTH - 1
    planned: list[str] = []
    n = numbers[-1] if numbers else 0
    while len(planned) < count:
        n += 1
        if n > limit:
            raise FatalError(f"Sequence would exceed {limit:0{SEQUENCE_WIDTH}d}; nothing created")
        email = f"{n:0{SEQUENCE_WIDTH}d}@{DOMAIN}"
        if email in existing or email in PROTECTED or email in EXCLUDE_FROM_SEQUENCE:
            continue
        planned.append(email)
    return planned
