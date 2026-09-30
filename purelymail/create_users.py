#!/usr/bin/env python3
"""Create Purelymail mailboxes for bgyhub.com, safely.

Only ever calls three endpoints: listUser, getUser (read-only) and createUser.
Existing users are skipped and never modified. Dry-run is the default; pass
--execute to actually create users.

Usage:
    python3 create_users.py --count 10              # preview the next 10 numbered mailboxes
    python3 create_users.py --count 10 --execute    # create them (asks for confirmation)
    python3 create_users.py                         # dry-run for the addresses in users.txt
    python3 create_users.py --offline               # users.txt dry-run, no network at all
    python3 create_users.py --execute               # create users.txt addresses (asks for confirmation)
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import secrets
import ssl
import string
import sys
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
        OUTPUT_DIR.mkdir(mode=0o700, exist_ok=True)
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


def setup_logging(verbose: bool) -> None:
    OUTPUT_DIR.mkdir(mode=0o700, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%dT%H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    fileh = logging.FileHandler(OUTPUT_DIR / "run.log")
    fileh.setFormatter(fmt)
    log.addHandler(console)
    log.addHandler(fileh)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)


# --------------------------------------------------------------------------- main flow


def validate(email: str) -> str | None:
    """Return a refusal status, or None if the address may be processed."""
    if email in PROTECTED:
        return "refused_protected"
    if not EMAIL_RE.match(email):
        return "refused_invalid"
    return None


def plan_sequence(existing: set[str], count: int) -> list[str]:
    """Next `count` numbered addresses after the highest existing one."""
    numbers = sorted(
        int(m.group(1))
        for e in existing - EXCLUDE_FROM_SEQUENCE
        if (m := SEQUENCE_RE.match(e))
    )
    shown = ", ".join(f"{n:0{SEQUENCE_WIDTH}d}" for n in numbers) or "none"
    log.info("Existing numbered mailboxes: %s (highest: %s)", shown,
             f"{numbers[-1]:0{SEQUENCE_WIDTH}d}" if numbers else "none")

    limit = 10 ** SEQUENCE_WIDTH - 1
    planned: list[str] = []
    n = numbers[-1] if numbers else 0
    while len(planned) < count:
        n += 1
        if n > limit:
            raise FatalError(f"Sequence would exceed {limit:0{SEQUENCE_WIDTH}d}; nothing created")
        email = f"{n:0{SEQUENCE_WIDTH}d}@{DOMAIN}"
        if email in existing or email in PROTECTED:
            log.info("%s already exists or is excluded, skipping that number", email)
            continue
        planned.append(email)
    return planned


def confirm(to_create: list[str]) -> None:
    """Show what will be created and require the user to type CREATE <n>."""
    print()
    print(f"Will create {len(to_create)} mailbox(es):")
    for e in to_create:
        print(f"  {e}")
    print("Settings: passwordReset=on, searchIndexing=on, welcomeEmail=off, recoveryEmail=none")
    if not sys.stdin.isatty():
        raise FatalError("Confirmation needs an interactive terminal; nothing created")
    phrase = f"CREATE {len(to_create)}"
    try:
        answer = input(f"Type {phrase} to proceed (anything else cancels): ")
    except EOFError:
        answer = ""
    if answer.strip() != phrase:
        raise FatalError("Not confirmed; nothing created")
    log.info("Confirmed by user: %s", phrase)


def run(args: argparse.Namespace) -> int:
    client = None
    existing: set[str] = set()
    if not args.offline:
        client = PurelymailClient(load_token(), args.base_url)
        log.info("Calling listUser to find existing users")
        existing = client.list_users()
        log.info("listUser returned %d existing user(s)", len(existing))

    if args.count is not None:
        emails = plan_sequence(existing, args.count)
        source = f"--count {args.count}"
    else:
        emails = load_emails(args.users)
        if not emails:
            raise FatalError(f"No addresses in {args.users}")
        source = args.users.name

    mode = "EXECUTE" if args.execute else ("DRY-RUN (offline)" if args.offline else "DRY-RUN (read-only API checks)")
    log.info("Mode: %s | %d address(es) from %s", mode, len(emails), source)

    if args.execute:
        to_create = [e for e in emails if validate(e) is None and e not in existing]
        if not to_create:
            log.info("Nothing to create")
        else:
            confirm(to_create)

    csv_path = OUTPUT_DIR / ("credentials.csv" if args.execute else "credentials.dryrun.csv")
    out = CsvLog(csv_path)
    counts: dict[str, int] = {}
    consecutive_errors = 0

    def record(email: str, status: str, password: str = "") -> None:
        out.write(email, status, password)
        counts[status] = counts.get(status, 0) + 1

    try:
        for email in emails:
            refusal = validate(email)
            if refusal:
                log.warning("%s: %s, not touching it", email, refusal)
                record(email, refusal)
                continue

            if email in existing:
                log.info("%s: already exists (listUser), skipping", email)
                record(email, "skipped_exists" if args.execute else "would_skip_exists")
                continue

            if args.offline:
                log.info("%s: would create (offline, existence not checked)", email)
                record(email, "would_create")
                continue

            # Second existence check right before creating.
            try:
                details = client.get_user(email)
            except ApiError as e:
                log.error("%s: getUser pre-check inconclusive (%s); NOT creating", email, e)
                record(email, "error_precheck")
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    raise FatalError(f"{consecutive_errors} errors in a row, stopping")
                continue
            if details is not None:
                log.info("%s: already exists (getUser), skipping", email)
                record(email, "skipped_exists" if args.execute else "would_skip_exists")
                continue

            if not args.execute:
                log.info("%s: does not exist, would create", email)
                record(email, "would_create")
                continue

            password = generate_password()
            # Save the password before calling the API so it is never lost.
            out.write(email, "pending", password)
            log.info("%s: calling createUser", email)
            try:
                client.create_user(email, password)
            except ApiError as e:
                if e.ambiguous:
                    log.error("%s: createUser outcome UNKNOWN (%s). Check the dashboard; not retrying.", email, e)
                    record(email, "unknown", password)
                else:
                    log.error("%s: createUser failed (%s)", email, e)
                    record(email, "error", password)
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    raise FatalError(f"{consecutive_errors} errors in a row, stopping")
                continue

            consecutive_errors = 0
            record(email, "created", password)
            log.info("%s: created", email)

            # Verify settings (read-only). Spam filtering cannot be set via the API.
            try:
                d = client.get_user(email) or {}
                log.info(
                    "%s: verify searchIndexing=%s passwordReset=%s spamFiltering=%s",
                    email, d.get("enableSearchIndexing"), d.get("recoveryEnabled"), d.get("enableSpamFiltering"),
                )
                if d.get("enableSpamFiltering") is not True:
                    log.warning("%s: spam filtering is NOT on; enable it in the Purelymail dashboard", email)
            except ApiError as e:
                log.warning("%s: created, but post-create getUser failed (%s)", email, e)
    finally:
        out.close()

    log.info("Summary: %s", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing done")
    log.info("Results written to %s", csv_path.relative_to(SCRIPT_DIR))
    return 1 if any(k.startswith(("error", "unknown")) for k in counts) else 0


def main() -> int:
    p = argparse.ArgumentParser(description="Create Purelymail users for bgyhub.com (dry-run by default).")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--users", type=Path, default=DEFAULT_USERS_FILE, help="file with one address per line")
    src.add_argument("--count", type=int, help=f"create the next N numbered mailboxes (1-{MAX_COUNT})")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--execute", action="store_true", help="actually create missing users")
    g.add_argument("--offline", action="store_true", help="dry-run without any network calls")
    p.add_argument("--base-url", default=API_BASE, help=argparse.SUPPRESS)  # for local testing only
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    if args.count is not None:
        if not 1 <= args.count <= MAX_COUNT:
            p.error(f"--count must be between 1 and {MAX_COUNT}")
        if args.offline:
            p.error("--count needs listUser to find the next number; it cannot run with --offline")

    if args.base_url != API_BASE and not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", args.base_url):
        p.error("--base-url may only point at localhost (testing)")

    setup_logging(args.verbose)
    try:
        return run(args)
    except FatalError as e:
        log.error("Aborted: %s", e)
        return 2
    except ApiError as e:
        log.error("Aborted: %s", e)
        return 2
    except KeyboardInterrupt:
        log.error("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
