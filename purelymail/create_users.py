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
import logging
import re
import sys
from pathlib import Path

from mailbox_core import (
    API_BASE, DEFAULT_USERS_FILE, MAX_CONSECUTIVE_ERRORS, MAX_COUNT, OUTPUT_DIR, SCRIPT_DIR,
    SEQUENCE_WIDTH, ApiError, CsvLog, FatalError, PurelymailClient, existing_sequence_numbers,
    generate_password, load_emails, load_token, log, next_sequence, validate,
)


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


def plan_sequence(existing: set[str], count: int) -> list[str]:
    numbers = existing_sequence_numbers(existing)
    shown = ", ".join(f"{n:0{SEQUENCE_WIDTH}d}" for n in numbers) or "none"
    log.info("Existing numbered mailboxes: %s (highest: %s)", shown,
             f"{numbers[-1]:0{SEQUENCE_WIDTH}d}" if numbers else "none")
    return next_sequence(existing, count)


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
