# BGYHub Mailbox Admin (Purelymail, bgyhub.com)

A small local web app on your Mac for creating `@bgyhub.com` mailboxes on
Purelymail, resetting their passwords, and keeping the **BGYHub email tracking
workbook** (`.xlsx`) up to date as the master record.

It runs only on your Mac at `http://127.0.0.1:8787`. Nothing is reachable from
other computers.

## Start it (double-click)

**First time only:**

1. Make sure `purelymail/.env` contains your token: `PURELYMAIL_API_TOKEN=...`
   (it's already there if you used the command-line script before).
2. In Finder, open the `BGYHub/purelymail` folder and double-click
   **`Start Mailbox Admin.command`**.
   - If macOS says it can't be opened, right-click it, choose **Open**, then **Open** again.
   - The first start installs what the app needs (about a minute), then opens your browser.
   - It also puts a **BGYHub Mailbox Admin** icon on your Desktop.
3. In the browser, create your **admin password**. It unlocks the app and
   encrypts the stored mailbox passwords. It can't be recovered, so save it in
   your password manager.
4. Click **Choose workbook**, then **Find my tracking workbook**, and pick your
   tracking file. The app searches Desktop, Documents, Downloads and iCloud Drive.
   You can also paste the file's full path.
5. Click **Import now** to bring in 001–003. This copies their passwords from
   `output/credentials.csv` into the workbook and lets you reset them from the app.

**Every day after that:** double-click **BGYHub Mailbox Admin** on your Desktop.
If the app is already running, the icon just opens it in the browser.

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
- **Stored passwords:** they're encrypted (AES-256-GCM) in `admin/data/admin.db`.
  The encryption key is unlocked by your admin password and kept only in
  memory while the app is unlocked. The app locks itself after 30 minutes of
  inactivity.
- **Passwords in other files:** passwords appear only in the tracking
  workbook, its backups, `output/credentials.csv` and exports you download.
  They never appear in `output/admin.log` or `output/run.log`.
- **Web protections:** the app listens on `127.0.0.1` only and rejects other
  host names. Every change requires a CSRF token, and cookies are
  same-site only.
- **Excluded from Git:** `.env`, `output/`, `admin/data/` and `.venv/`.
  This repository is **public**, so keep it that way.
- **Plaintext copies:** the workbook and its backups hold passwords in plain
  text (file mode 600, so only your user can read them). Avoid keeping the
  workbook in a folder that syncs to other services if you can.

## Files

```
Start Mailbox Admin.command   double-click launcher (also creates the Desktop icon)
admin/                        the web app (app.py, service.py, store.py, templates, static, launch.sh)
tracking_xlsx.py              safe workbook updates
mailbox_core.py               Purelymail API client, numbering, passwords (shared)
create_users.py               command-line tool (advanced, see below)
tests/                        tests against a fake Purelymail server
```

## Troubleshooting

| Problem | Fix |
|---|---|
| "Python 3 is not installed" | Install Python from python.org, then double-click again. |
| "Port 8787 is already used" | Another program uses that port. Quit it, or restart the Mac. |
| Token shows ❌ Missing | Put `PURELYMAIL_API_TOKEN=...` in `purelymail/.env`, then Quit and start again. |
| Workbook shows ❌ Problem | The message says why, for example that the sheet or table was renamed. |
| Forgot the admin password | Quit the app and delete `admin/data/admin.db`. You'll set a new password and need to import again. Mailboxes and the workbook are not affected. |
| Something else | See `output/admin-server.log` and `output/admin.log`. |

If you move the `BGYHub` folder, delete the Desktop icon and double-click
`Start Mailbox Admin.command` again to recreate it.

## Command-line tool (advanced)

`create_users.py` still works, but it **doesn't update the tracking workbook**.
For normal use, prefer the app.

```sh
python3 create_users.py --count 10             # preview (read-only)
python3 create_users.py --count 10 --execute   # create; asks you to type CREATE 10
```

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -t . -v
```
