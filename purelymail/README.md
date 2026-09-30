# Purelymail user creation (bgyhub.com)

`create_users.py` creates the mailboxes listed in `users.txt`. It only uses
Python 3's standard library, so there is nothing to install.

It calls just three endpoints: `listUser` and `getUser` (both read-only) and
`createUser`. It never calls `modifyUser`, `deleteUser` or any password reset
endpoint. It skips existing users and never changes them. It refuses
`service@bgyhub.com` and any address outside `@bgyhub.com`.

## Setup (macOS)

```sh
cd purelymail
cp .env.example .env
chmod 600 .env
# edit .env and paste your token:  PURELYMAIL_API_TOKEN=...
```

Instead of the `.env` file, you can export `PURELYMAIL_API_TOKEN` in your
shell. The environment variable takes priority over `.env`.

## Run

```sh
python3 create_users.py --offline     # dry-run, no network at all
python3 create_users.py               # dry-run, read-only listUser/getUser checks
python3 create_users.py --execute     # create the missing users
```

Always run the read-only dry-run before `--execute`. It shows what the
script will do, and confirms that `getUser` recognises a user that doesn't
exist. If `getUser` doesn't, that user shows up as `error_precheck` and
the script won't create it.

## Output (Git ignores all of it)

| File | Contents |
|---|---|
| `output/credentials.csv` | `email,password,status,created_at` from `--execute` runs (mode 0600) |
| `output/credentials.dryrun.csv` | dry-run results (no passwords) |
| `output/run.log` | log without tokens or passwords |

The CSV is append-only. Before calling `createUser`, the script writes the
new password to the CSV with status `pending`, so the password isn't lost if
the script crashes. The final status then goes on a new row.

Statuses:

| Status | Meaning |
|---|---|
| `created` | the user was created |
| `skipped_exists` | the user already existed, so the script left it alone |
| `unknown` | there was a timeout or 5xx error during create. Check the dashboard; the script doesn't retry. |
| `error` | Purelymail rejected the create request |
| `error_precheck` | the existence check was inconclusive, so the script didn't create the user |
| `refused_protected` / `refused_invalid` | the address was never sent to the API |

## Behaviour

- **Passwords:** 24 random characters from Python's `secrets` module, using
  upper case, lower case, digits and symbols. A different password is
  generated for every user.
- **Settings sent with `createUser`:** `enablePasswordReset: true`,
  `enableSearchIndexing: true`, `sendWelcomeEmail: false`. No recovery email
  is set, so resets won't have anywhere to go until you add one in the
  dashboard.
- **Spam filtering:** the API can't set it. After creating each user, the
  script reads the user back with `getUser` and logs a warning if spam
  filtering is off.
- **Rate limiting:** at least 2 seconds between requests.
- **Retries:** read-only calls retry at most twice (after 5 seconds, then 15
  seconds) on 429, 5xx or network errors. `createUser` retries only on 429,
  never when the outcome is ambiguous.
- **Stopping:** the script aborts after 3 errors in a row, or right away if
  the token is rejected (HTTP 401 or 403).
- **TLS errors on macOS:** with python.org Python, run
  `/Applications/Python 3.x/Install Certificates.command` or
  `pip3 install certifi`.
