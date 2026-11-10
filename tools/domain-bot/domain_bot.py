#!/usr/bin/env python3
"""Cheap-domain bot for the Porkbun API (v3).

Finds the cheapest available domains among the names you give it and,
only when told to with --confirm, registers them from your account credit.

No third-party packages needed. Credentials come from the environment:

    PORKBUN_API_KEY      pk1_...   (pk1_sb_... = sandbox, nothing is charged)
    PORKBUN_SECRET_KEY   sk1_...

Commands:

    prices      cheapest TLDs by list price (no key needed)
    check       availability and price of candidate names
    buy         register the cheapest matches (trial run unless --confirm)
    cloudflare  move a registered domain's DNS to your Cloudflare account

Give check/buy no names and they invent random ones and try them on the
cheapest TLDs currently on sale.
"""

import argparse
import datetime
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal

API = "https://api.porkbun.com/api/json/v3"
BULK_CHECK_SIZE = 25  # API maximum per checkDomain call
MAX_COUNT = 10  # hard ceiling on registrations per run
DEFAULT_TLDS = "xyz,site,online,store,click,fun,space,website,com"
AUTO_NAMES = 2  # random names x cheapest TLDs = one bulk check
AUTO_TLDS = 5
ACCOUNT_ERRORS = {"VERIFICATION_REQUIRED", "MONTHLY_SPEND_LIMIT_EXCEEDED", "INSUFFICIENT_PERMISSIONS"}
LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


class ApiError(Exception):
    def __init__(self, code, message, body):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.body = body


def call(path, body=None, auth=True, idempotency_key=None):
    """POST (or GET when body is None) and return the parsed JSON response."""
    headers = {"Content-Type": "application/json", "User-Agent": "domain-bot/1.0"}
    if auth:
        key = os.environ.get("PORKBUN_API_KEY")
        secret = os.environ.get("PORKBUN_SECRET_KEY")
        if not key or not secret:
            sys.exit("Set PORKBUN_API_KEY and PORKBUN_SECRET_KEY first (see README).")
        headers["X-API-Key"] = key
        headers["X-Secret-API-Key"] = secret
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, headers=headers)

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as err:
            raw = err.read()
        try:
            out = json.loads(raw)
        except ValueError:
            raise ApiError("BAD_RESPONSE", raw[:200].decode(errors="replace"), {})
        if out.get("status") == "SUCCESS":
            return out
        if out.get("code") == "RATE_LIMIT_EXCEEDED" and attempt < 2:
            wait = int(out.get("retryAfter") or out.get("ttlRemaining") or 10)
            if wait > 120:  # e.g. the 24h scanning penalty: do not sit and wait
                raise ApiError(out.get("code"), out.get("message", ""), out)
            print(f"  rate limited, waiting {wait}s ...", file=sys.stderr)
            time.sleep(wait + 1)
            continue
        raise ApiError(out.get("code", "ERROR"), out.get("message", ""), out)


def expand(names, tlds):
    """'foo' -> foo.<each tld>; 'foo.com' stays as it is."""
    tlds = [t.strip().lower().lstrip(".") for t in tlds if t.strip()]
    out = []
    for name in names:
        name = name.strip().lower().rstrip(".")
        if not name:
            continue
        candidates = [name] if "." in name else [f"{name}.{t}" for t in tlds]
        for c in candidates:
            # Refuse odd spellings here: the API would silently "fix" them
            # (bad_name -> badname) and we would buy a name nobody typed.
            if not all(LABEL.match(label) for label in c.split(".")):
                sys.exit(f"'{c}' is not a valid domain name (use a-z, 0-9 and '-').")
            if c not in out:
                out.append(c)
    return out


def check_batch(domains):
    """One bulk call, split per TLD when a slow registry makes the batch too big."""
    try:
        return [call("/domain/checkDomain", {"domains": domains})]
    except ApiError as err:
        if err.code != "BULK_CHECK_TOO_SLOW" or len(domains) == 1:
            raise
    by_tld = {}
    for d in domains:
        by_tld.setdefault(d.rsplit(".", 1)[-1], []).append(d)
    if len(by_tld) == 1:  # a one-domain-per-command registry such as .de
        return [r for d in domains for r in check_batch([d])]
    return [r for group in by_tld.values() for r in check_batch(group)]


def check(domains):
    """Return ({domain: info}, [invalid entries]) using the bulk endpoint."""
    found, invalid = {}, []
    for i in range(0, len(domains), BULK_CHECK_SIZE):
        pending = domains[i : i + BULK_CHECK_SIZE]
        for _ in range(2):  # 'unresolved' means the registry timed out: retry once
            unresolved = []
            for res in check_batch(pending):
                found.update(res.get("domains") or {})
                invalid.extend(res.get("invalid") or [])
                unresolved.extend(res.get("unresolved") or [])
            pending = unresolved
            if not pending:
                break
        for d in pending:
            invalid.append({"domain": d, "reason": "registry did not answer"})
    return found, invalid


def tld_prices(years, max_renewal=None):
    """Normal-DNS TLDs on sale, cheapest first: [(total, first year, renewal, tld)]."""
    res = call("/pricing/get", auth=False)
    rows = []
    for tld, p in res["pricing"].items():
        try:
            reg, ren = Decimal(p["registration"]), Decimal(p["renewal"])
        except Exception:
            continue
        if reg <= 0:  # listed but not on sale yet
            continue
        if p.get("specialType"):  # Handshake names: not part of the normal DNS
            continue
        if max_renewal is not None and ren > max_renewal:
            continue
        rows.append((reg + ren * (years - 1), reg, ren, tld))
    rows.sort()
    return rows


def random_name():
    """A pronounceable throwaway label such as 'kovamiru42'."""
    word = "".join(
        secrets.choice("bdfgklmnprstvz") + secrets.choice("aeiou") for _ in range(4)
    )
    return f"{word}{secrets.randbelow(90) + 10}"


def candidates(args):
    """Domains to look at: the user's names, or random ones on the cheapest TLDs."""
    if args.names:
        return expand(args.names, (args.tlds or DEFAULT_TLDS).split(","))
    if args.tlds:
        tlds = args.tlds.split(",")
    else:
        skip = {t.strip().lower().lstrip(".") for t in args.exclude_tlds.split(",")}
        cheapest = tld_prices(args.years, getattr(args, "max_renewal", None))
        tlds = [row[3] for row in cheapest if row[3] not in skip][:AUTO_TLDS]
    names = [random_name() for _ in range(AUTO_NAMES)]
    print(f"No names given: trying random names on {', '.join('.' + t for t in tlds)}\n")
    return expand(names, tlds)


def special_tlds(domains):
    """TLDs outside the normal DNS (Handshake): cheap, but they do not resolve
    for ordinary browsers or mail servers, so they are useless for email."""
    tlds = sorted({d.rsplit(".", 1)[-1] for d in domains})
    if not tlds:
        return set()
    try:
        res = call("/pricing/get", {"tlds": tlds}, auth=False)
    except ApiError:
        return set()
    return {t for t, p in res.get("pricing", {}).items() if p.get("specialType")}


def offers(found, years):
    """Turn check results into sortable rows for the available names."""
    special = special_tlds([d for d, i in found.items() if i.get("avail") == "yes"])
    rows = []
    for domain, info in found.items():
        if info.get("avail") != "yes":
            continue
        price = Decimal(info["price"])
        renewal = Decimal(info.get("additional", {}).get("renewal", {}).get("price", "0"))
        rows.append(
            {
                "domain": domain,
                "price": price,
                "renewal": renewal,
                "total": price + renewal * (years - 1),
                "premium": info.get("premium") == "yes",
                "special": domain.rsplit(".", 1)[-1] in special,
            }
        )
    rows.sort(key=lambda r: (r["total"], r["domain"]))
    return rows


def eligible(row, args):
    if row["special"]:
        return "Handshake TLD: does not work on the normal internet"
    if row["premium"]:
        return "premium names cannot be bought through the API"
    if row["price"] > args.max_price:
        return f"first year ${row['price']} is over --max-price"
    if args.max_renewal is not None and row["renewal"] > args.max_renewal:
        return f"renewal ${row['renewal']} is over --max-renewal"
    return None


def print_rows(rows, years, args=None):
    print(f"{'domain':<34}{'1st year':>10}{'renewal':>10}{f'{years}y total':>11}  note")
    for r in rows:
        note = (eligible(r, args) or "") if args else ("not real DNS" if r["special"] else "premium" if r["premium"] else "")
        print(f"{r['domain']:<34}{r['price']:>10}{r['renewal']:>10}{r['total']:>11}  {note}")


def cmd_prices(args):
    rows = tld_prices(args.years, args.max_renewal)
    print(f"{'tld':<16}{'1st year':>10}{'renewal':>10}{f'{args.years}y total':>11}")
    for total, reg, ren, tld in rows[: args.top]:
        print(f"{'.' + tld:<16}{reg:>10}{ren:>10}{total:>11}")
    print("\nList prices in USD. A specific name can cost more (premium) or less.")


def cmd_check(args):
    domains = candidates(args)
    found, invalid = check(domains)
    rows = offers(found, args.years)
    if rows:
        print_rows(rows, args.years)
    taken = [d for d, i in found.items() if i.get("avail") != "yes"]
    if taken:
        print("\ntaken: " + ", ".join(sorted(taken)))
    for bad in invalid:
        print(f"skipped {bad['domain']}: {bad['reason']}")
    if not rows:
        print("\nNothing available. Try other names or TLDs.")


def log_purchase(path, entry):
    entry["time"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def cmd_buy(args):
    if not 1 <= args.count <= MAX_COUNT:
        sys.exit(f"--count must be between 1 and {MAX_COUNT}.")
    if args.confirm and not args.agree_to_terms:
        sys.exit(
            "--confirm also needs --agree-to-terms: registering means you accept Porkbun's\n"
            "Domain Name Registration Agreement and terms (https://porkbun.com/legal)."
        )
    sandbox = os.environ.get("PORKBUN_API_KEY", "").startswith("pk1_sb_")
    mode = "SANDBOX (fake money)" if sandbox else "LIVE"
    mode += ", real purchase" if args.confirm else ", trial run: nothing will be bought"
    print(f"[{mode}]\n")

    use_cloudflare = args.cloudflare and cloudflare_ready()

    domains = candidates(args)
    found, invalid = check(domains)
    rows = offers(found, args.years)
    for bad in invalid:
        print(f"skipped {bad['domain']}: {bad['reason']}")
    if not rows:
        sys.exit("None of the candidates is available.")
    print_rows(rows, args.years, args)
    print()

    budget_cents = int(args.budget * 100)
    spent_cents, bought, failed = 0, [], 0
    for row in rows:
        if len(bought) >= args.count:
            break
        if eligible(row, args):
            continue
        domain = row["domain"]
        try:
            # Ask the API for the exact charge; it also checks funds and limits.
            quote = call(
                f"/domain/create/{domain}",
                {"cost": 0, "agreeToTerms": "yes", "dryRun": True},
            )
        except ApiError as err:
            if err.code in ACCOUNT_ERRORS:  # same answer for every name: stop here
                sys.exit(f"Porkbun refused the registration: {err}\nFix this at https://porkbun.com/account and run again.")
            print(f"- {domain}: cannot be registered ({err})")
            continue
        cost = quote["cost"]
        if cost > int(args.max_price * 100):
            print(f"- {domain}: quoted {quote['costDisplay']}, over --max-price, skipped")
            continue
        if spent_cents + cost > budget_cents:
            print(f"- {domain}: {quote['costDisplay']} would exceed --budget ${args.budget}, stopping")
            break
        if not quote.get("wouldSucceed"):
            print(f"- {domain}: {quote.get('message')}")
            print("  Add credit at https://porkbun.com/account/credit or raise the API spend limit.")
            break

        if not args.confirm:
            print(f"+ {domain}: would register for {quote['costDisplay']}")
            spent_cents += cost
            bought.append(domain)
            continue

        try:
            res = call(
                f"/domain/create/{domain}",
                {"cost": cost, "agreeToTerms": "yes"},
                idempotency_key=str(uuid.uuid4()),
            )
        except ApiError as err:
            failed += 1
            print(f"- {domain}: registration failed ({err})")
            log_purchase(args.log, {"domain": domain, "ok": False, "error": str(err)})
            time.sleep(1.1)
            continue
        spent_cents += res["cost"]
        bought.append(domain)
        print(f"+ {domain}: REGISTERED for ${Decimal(res['cost']) / 100} (order {res.get('orderId')})")
        log_purchase(
            args.log,
            {
                "domain": domain,
                "ok": True,
                "cost_cents": res["cost"],
                "orderId": res.get("orderId"),
                "sandbox": bool(res.get("sandbox")),
            },
        )
        if args.no_auto_renew:
            try:
                call(f"/domain/updateAutoRenew/{domain}", {"status": "off"})
                print("  auto-renew turned off")
            except ApiError as err:
                print(f"  could NOT turn off auto-renew ({err}); do it on porkbun.com")
        time.sleep(1.1)  # the API allows one registration attempt per second

    total = Decimal(spent_cents) / 100
    if args.confirm:
        print(f"\nRegistered {len(bought)} domain(s), spent ${total}. Log: {args.log}")
        if use_cloudflare and bought:
            print()
            cloudflare_connect(bought)
    else:
        print(f"\nTrial run: would register {len(bought)} domain(s) for ${total}.")
        print("Re-run with --confirm --agree-to-terms to buy.")
    if failed or not bought:
        sys.exit(1)


def cloudflare_ready():
    """True when the Porkbun account is linked to a Cloudflare account."""
    try:
        res = call("/cloudflare/getConnection")
    except ApiError as err:
        if err.code == "SANDBOX_UNSUPPORTED":
            print("Cloudflare cannot be simulated with a sandbox key; skipping that step.\n")
            return False
        raise
    if res.get("connected"):
        return True
    url = res.get("connectUrl") or "https://porkbun.com/account/connectCloudflare"
    sys.exit(
        "Your Porkbun account is not linked to Cloudflare yet. Open this page in a\n"
        f"browser, approve the access, then run the command again:\n  {url}"
    )


def cloudflare_connect(domains):
    """Queue domains for the move: Porkbun creates the zone in the user's
    Cloudflare account, copies the DNS records and repoints the nameservers."""
    res = call("/cloudflare/connect", {"domains": domains}, idempotency_key=str(uuid.uuid4()))
    for group in ("queued", "alreadyQueued", "skipped"):
        for name, why in (res.get(group) or {}).items():  # {domain: note}
            print(f"Cloudflare {group}: {name}. {why}".rstrip(". "))
    print(
        "The move runs in the background: minutes for the zone, up to 72h for\n"
        "Cloudflare to mark it active. Check with:  domain_bot.py cloudflare status <domain>"
    )


def cmd_cloudflare(args):
    domains = expand([d for d in args.domains if "." in d], [])
    if len(domains) != len(args.domains):
        sys.exit("Give full domain names, e.g. example.xyz.")
    if args.action == "connect":
        if cloudflare_ready():
            cloudflare_connect(domains)
        return
    for domain in domains:
        try:
            res = call(f"/cloudflare/get/{domain}")
        except ApiError as err:
            print(f"{domain}: {err}")
            continue
        row = res.get("queue") or {}
        print(f"{domain}: {row.get('status')} {row.get('message') or ''}".rstrip())
        ns = row.get("assignedNameservers")
        if ns:
            print("  nameservers: " + ", ".join(ns))


def main():
    money = Decimal
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("names", nargs="*", help="keywords (foo) or full domains (foo.com); none = random names")
        sp.add_argument("--tlds", default=None, help=f"TLDs tried for keywords (default {DEFAULT_TLDS}; cheapest on sale for random names)")
        sp.add_argument("--exclude-tlds", default="top", help="TLDs never picked for random names (default top)")
        sp.add_argument("--years", type=int, default=1, help="rank by total cost over this many years (default 1)")

    sp = sub.add_parser("prices", help="cheapest TLDs by list price")
    sp.add_argument("--years", type=int, default=1)
    sp.add_argument("--top", type=int, default=20)
    sp.add_argument("--max-renewal", type=money, default=None)
    sp.set_defaults(func=cmd_prices)

    sp = sub.add_parser("check", help="availability and price of candidates")
    common(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("buy", help="register the cheapest matches (trial run unless --confirm)")
    common(sp)
    sp.add_argument("--count", type=int, default=1, help=f"how many to register (default 1, max {MAX_COUNT})")
    sp.add_argument("--max-price", type=money, default=money("3"), help="max first-year price per domain, USD (default 3)")
    sp.add_argument("--max-renewal", type=money, default=None, help="skip domains whose yearly renewal is above this, USD")
    sp.add_argument("--budget", type=money, default=money("5"), help="max total spend for this run, USD (default 5)")
    sp.add_argument("--no-auto-renew", action="store_true", help="turn auto-renew off after registering")
    sp.add_argument("--cloudflare", action="store_true", help="after registering, move the domain's DNS to your Cloudflare account")
    sp.add_argument("--confirm", action="store_true", help="really buy; without it nothing is charged")
    sp.add_argument("--agree-to-terms", action="store_true", help="you accept Porkbun's registration agreement")
    sp.add_argument("--log", default="purchases.jsonl", help="purchase log file (default purchases.jsonl)")
    sp.set_defaults(func=cmd_buy)

    sp = sub.add_parser("cloudflare", help="move a registered domain's DNS to your Cloudflare account")
    sp.add_argument("action", choices=["connect", "status"])
    sp.add_argument("domains", nargs="+", help="full domain names")
    sp.set_defaults(func=cmd_cloudflare)

    args = p.parse_args()
    if getattr(args, "years", 1) < 1:
        sys.exit("--years must be at least 1.")
    try:
        args.func(args)
    except ApiError as err:
        sys.exit(f"Porkbun API error: {err}")
    except urllib.error.URLError as err:
        sys.exit(f"Network error: {err.reason}")


if __name__ == "__main__":
    main()
