# BGYHub Mailbox Admin (Purelymail, bgyhub.com)

A small local web app on your Mac for creating `@bgyhub.com` mailboxes on
Purelymail, resetting their passwords, and keeping the **BGYHub email tracking
workbook** (`.xlsx`) up to date as the master record.

It runs only on your Mac at `http://127.0.0.1:8787`. Nothing is reachable from
other computers.

## Start it (double-click, no Terminal)

The app is **`BGYHub Mailbox Admin.app`**. You get it as a zip file
(`BGYHub-Mailbox-Admin-app.zip`), or it's in this repository under
`purelymail/mac/`.

1. Double-click the zip in Finder's **Downloads** folder. This unpacks
   **BGYHub Mailbox Admin.app**.
2. Drag the app to your **Desktop** (or **Applications**).
3. Double-click it. The first time, macOS blocks it because it isn't from the
   App Store:
   - **macOS 15 (Sequoia) or newer:** click **Done**, then open **System
     Settings → Privacy & Security**. Scroll down to "BGYHub Mailbox Admin was
     blocked" and click **Open Anyway**, then **Open Anyway** again (and enter
     your Mac password if asked).
   - **macOS 14 or older:** right-click the app, choose **Open**, then **Open**.

   You only have to do this once.

Each time it's opened, the app:

- **Gets the code:** downloads or updates the code in `~/BGYHub`, from the
  `claude/bold-brahmagupta-tpfvmk` branch, fast-forward only.
- **Nothing to install:** all the Python packages the app needs are bundled
  in `vendor/`. It runs with any Python 3.9+ already on the Mac, including
  Apple's built-in one, with no pip and no download.
- **Opens the browser:** goes to `http://127.0.0.1:8787`, or just opens the
  page again if the app is already running.

**First time in the browser:**

1. **Create your admin password.** It unlocks the app and encrypts the stored
   mailbox passwords. It can't be recovered, so save it in your password
   manager.
2. **Choose workbook → Find my tracking workbook**, and pick your tracking
   file. You can also paste its full path.
3. **Import now.** This copies 001–003's passwords from
   `output/credentials.csv` into the workbook.

The Purelymail token must be in `~/BGYHub/purelymail/.env` as
`PURELYMAIL_API_TOKEN=...`. It's already there if you used the command-line
script before. The app shows ❌ if it's missing.

`Start Mailbox Admin.command` (in `purelymail/`) does the same as the app
without the self-update step.

## Using it

| Button | What it does |
|---|---|
| **Preview** | Shows the next N addresses. The app numbers from the highest existing `NNN@bgyhub.com`, skips `123@` and anything that already exists, and never fills gaps. |
| **Create** | Asks you to confirm in a dialog, then creates the mailboxes one by one and shows Created / Failed / Unknown / Skipped for each, plus whether the Excel update worked. |
| **Export new credentials (CSV)** | Downloads that batch's addresses and passwords. Asks for your admin password first. |
| **Reset Password** | Only shown for mailboxes this app created or imported. You type the address to confirm, and the new password is shown once and written to the workbook. |
| **Open Tracking Excel** | Opens the workbook in Excel or Numbers. |
| **Download Tracking Excel** | Downloads a copy. Asks for your admin password first. |
| **Retry Excel update** | Appears when the workbook couldn't be updated (for example because it was open in Excel). |
| **Lock / Quit** | Lock asks for the password again. Quit stops the app. |

**Close the workbook in Excel before you create or reset.** While it's open,
the app won't write to it, because Excel would overwrite the changes when you
save. Changes made while it's open wait in a queue. Click **Retry Excel
update** after closing it.

## What gets written to the tracking workbook

The workbook is the master record. The app expects the sheet `已使用邮箱` with
the table `UsedEmailsTable`. The first time it writes, it adds a **密码**
column as column G at the end of the table.

| | 邮箱 | 状态 | 注册平台/用途 | 注册日期 | 付款卡/方式 | 备注 | 密码 |
|---|---|---|---|---|---|---|---|
| New mailbox | address | 未使用 | blank | creation date | blank | Purelymail 创建 | password |
| Password reset | – | – | – | – | – | – | **only this cell changes** |

- **When passwords are written:** a password is written only after Purelymail
  confirms the create or reset succeeded. A failed or unconfirmed mailbox is
  never added; it shows in the app and the log instead.
- **No duplicates:** there's never a second row for an address that already
  exists. For an existing row, the app only fills in cells that are empty, and
  never overwrites what you typed.
- **What's preserved:** existing rows, formatting, column widths, the 状态
  dropdown and the table style stay exactly as they were.

### How every workbook change is protected

1. One writer at a time. If Excel has the file open, the app doesn't write.
2. **A backup is made before every change**, in `admin/data/backups/`.
   The last 200 are kept.
3. The change is saved to a temporary file next to the workbook. That file is
   re-opened and checked: every existing cell, style, table, dropdown and
   column width must be unchanged, and only the intended cells may differ.
4. If someone changed the workbook in the meantime, the app stops.
5. Only then is the original replaced, in a single atomic step. If anything
   fails, the original is left exactly as it was.

The app refuses workbooks that contain macros, images, charts, pivot tables
or links to other files, because those could be lost when it saves.

## Security

- **API token:** it stays in `purelymail/.env` on your Mac and is used only by
  the local server. It is never sent to the browser or written to any log.
- **Stored passwords:** they're encrypted (HMAC-SHA256 encrypt-then-MAC, keys derived with
  PBKDF2) in `admin/data/admin.db`.
  The encryption key is unlocked by your admin password and kept only in
  memory while the app is unlocked. The app locks itself after 30 minutes of
  inactivity.
- **Passwords in other files:** passwords appear only in the tracking
  workbook, its backups, `output/credentials.csv` and exports you download.
  They never appear in `output/admin.log` or `output/run.log`.
- **Web protections:** the app listens on `127.0.0.1` only and rejects other
  host names. Every change requires a CSRF token, and cookies are
  same-site only.
- **Excluded from Git:** `.env`, `output/` and `admin/data/`.
  This repository is **public**, so keep it that way.
- **Plaintext copies:** the workbook and its backups hold passwords in plain
  text (file mode 600, so only your user can read them). Avoid keeping the
  workbook in a folder that syncs to other services if you can.

## Files

```
mac/BGYHub Mailbox Admin.app  the double-click app (self-updating launcher)
Start Mailbox Admin.command   alternative launcher
admin/                        the web app (app.py, service.py, store.py, templates, static, launch.sh)
tracking_xlsx.py              safe workbook updates
mailbox_core.py               Purelymail API client, numbering, passwords (shared)
create_users.py               command-line tool (advanced, see below)
tests/                        tests against a fake Purelymail server
vendor/                       bundled pure-Python packages (Flask, openpyxl, …)
```

## Troubleshooting

| Problem | Fix |
|---|---|
| "No usable Python 3.9+ was found" | Install Python from python.org (or Apple's Command Line Tools), then double-click again. |
| "Port 8787 is already used" | Another program uses that port. Quit it, or restart the Mac. |
| Token shows ❌ Missing | Put `PURELYMAIL_API_TOKEN=...` in `purelymail/.env`, then Quit and start again. |
| Workbook shows ❌ Problem | The message says why, for example that the sheet or table was renamed. |
| Forgot the admin password | Quit the app and delete `admin/data/admin.db`. You'll set a new password and need to import again. Mailboxes and the workbook are not affected. |
| Something else | See `output/admin-server.log` and `output/admin.log`. |

The app always uses the folder `~/BGYHub` (your home folder → BGYHub). Don't move or rename it.

## Command-line tool (advanced)

`create_users.py` still works, but it **doesn't update the tracking workbook**.
For normal use, prefer the app.

```sh
python3 create_users.py --count 10             # preview (read-only)
python3 create_users.py --count 10 --execute   # create; asks you to type CREATE 10
```

## Tests

```sh
python3 -m unittest discover -s tests -t . -v   # uses the bundled packages
```
