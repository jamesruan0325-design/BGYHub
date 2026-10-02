"""Safe updates to the BGYHub email tracking workbook (the master record).

Every write:
  1. takes an exclusive lock (one writer at a time),
  2. refuses if the workbook is open in Excel (its ~$ lock file exists),
  3. fingerprints the file, then copies it to the backup folder,
  4. edits a copy in memory and saves it to a temp file next to the original,
  5. re-opens the temp file and verifies every pre-existing cell, style, table,
     dropdown, conditional format, merged range and column width is unchanged
     and that only the intended cells changed,
  6. checks nobody modified the original meanwhile,
  7. atomically replaces the original (os.replace).
Any failure leaves the original byte-for-byte untouched.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import os
import shutil
import tempfile
import warnings
from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.worksheet.table import TableColumn

SHEET = "已使用邮箱"
TABLE = "UsedEmailsTable"
BASE_HEADERS = ["邮箱", "状态", "注册平台/用途", "注册日期", "付款卡/方式", "备注"]
PASSWORD_HEADER = "密码"
COL = {"email": 1, "status": 2, "platform": 3, "date": 4, "payment": 5, "note": 6, "password": 7}

STATUS_NEW = "未使用"
NOTE_CREATED = "Purelymail 创建"
NOTE_RESET = "Purelymail 重置密码"
DATE_FORMAT = "yyyy-mm-dd"
PASSWORD_COLUMN_WIDTH = 28

KEEP_BACKUPS = 200
SEARCH_MAX_DEPTH = 3
SEARCH_MAX_FILES = 300
SEARCH_SKIP_DIRS = {"Library", "node_modules", ".git", ".venv", "Applications", "backups", "vendor"}

PRIVACY_HINT = ("macOS privacy settings blocked access to this location. Move the workbook into the "
                "BGYHub folder in your home folder (not protected), or allow access in System Settings → "
                "Privacy & Security → Files and Folders.")




class TrackingError(Exception):
    """The workbook was not modified."""


class ExcelOpenError(TrackingError):
    pass


@dataclass
class RowOp:
    kind: str  # "create" | "import" | "reset"
    email: str
    password: str
    date: dt.date | None = None


@dataclass
class ApplyResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    password_column_added: bool = False
    backup: Path | None = None

    @property
    def written(self) -> bool:
        return bool(self.added or self.updated or self.password_column_added)


# --------------------------------------------------------------------------- helpers


def _norm(value) -> str:
    return str(value).strip().lower() if value is not None else ""


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _as_date(value):
    return value.date() if isinstance(value, dt.datetime) else value


def excel_lock_files(path: Path) -> list[Path]:
    """Excel/Office keep '~$<name>' (or '~$' + name[2:]) next to an open file."""
    names = {"~$" + path.name, "~$" + path.name[2:]}
    return [path.with_name(n) for n in names if path.with_name(n).exists()]


def fingerprint(path: Path) -> tuple[int, int, str]:
    st = path.stat()
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return st.st_size, st.st_mtime_ns, h


@contextmanager
def _exclusive(lock_path: Path):
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(lock_path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _load(path: Path):
    """Load the workbook, refusing anything openpyxl would silently drop."""
    if path.suffix.lower() != ".xlsx":
        raise TrackingError(f"{path.name}: only .xlsx files are supported")
    try:
        if not path.is_file():
            raise TrackingError(f"{path}: file not found")
    except PermissionError:  # Python 3.9 raises for EPERM (macOS privacy) instead of returning False
        raise TrackingError(f"{path.name}: {PRIVACY_HINT}") from None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            wb = openpyxl.load_workbook(path)
        except Exception as e:  # corrupt / not a workbook
            raise TrackingError(f"{path.name}: cannot be read as a workbook ({e})") from None
    dropped = [str(w.message) for w in caught if "remove" in str(w.message).lower()
               or "not supported" in str(w.message).lower()]
    if dropped:
        raise TrackingError(f"{path.name}: contains features that could be lost on save: {dropped[0]}")
    if getattr(wb, "vba_archive", None) is not None:
        raise TrackingError(f"{path.name}: contains macros")
    if getattr(wb, "_external_links", None):
        raise TrackingError(f"{path.name}: contains links to other files")
    for ws in wb.worksheets:
        if getattr(ws, "_images", None) or getattr(ws, "_charts", None) or getattr(ws, "_pivots", None):
            raise TrackingError(f"{path.name}: sheet '{ws.title}' contains images, charts or pivot tables")
    if len(wb.chartsheets):
        raise TrackingError(f"{path.name}: contains chart sheets")
    return wb


def _locate_table(wb):
    if SHEET not in wb.sheetnames:
        raise TrackingError(f"sheet '{SHEET}' not found")
    ws = wb[SHEET]
    if TABLE not in ws.tables:
        raise TrackingError(f"table '{TABLE}' not found on sheet '{SHEET}'")
    table = ws.tables[TABLE]
    min_col, min_row, max_col, max_row = range_boundaries(table.ref)
    if (min_col, min_row) != (1, 1):
        raise TrackingError(f"table '{TABLE}' must start at A1 (found {table.ref})")
    headers = [ws.cell(1, c).value for c in range(1, max_col + 1)]
    if headers == BASE_HEADERS:
        has_pw = False
    elif headers == BASE_HEADERS + [PASSWORD_HEADER]:
        has_pw = True
    else:
        raise TrackingError(f"unexpected table headers {headers}; expected {BASE_HEADERS} (+ {PASSWORD_HEADER})")
    return ws, table, max_row, has_pw


def _email_rows(ws, last_row: int) -> dict[str, list[int]]:
    rows: dict[str, list[int]] = {}
    for r in range(2, max(ws.max_row, last_row) + 1):
        e = _norm(ws.cell(r, COL["email"]).value)
        if e:
            rows.setdefault(e, []).append(r)
    return rows


def describe(path: Path) -> dict:
    """Validation summary for the UI. Never raises."""
    path = Path(path).expanduser()
    info = {"path": str(path), "valid": False, "reason": "", "rows": 0, "has_password_column": False,
            "emails": {}, "open_in_excel": False}
    try:
        info["open_in_excel"] = bool(path.exists() and excel_lock_files(path))
        wb = _load(path)
        ws, table, last_row, has_pw = _locate_table(wb)
        info.update(valid=True, rows=last_row - 1, has_password_column=has_pw,
                    mtime=dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"))
        for e, rows in _email_rows(ws, last_row).items():
            r = rows[0]
            info["emails"][e] = {
                "row": r, "duplicate": len(rows) > 1, "status": ws.cell(r, COL["status"]).value,
                "has_password": has_pw and not _blank(ws.cell(r, COL["password"]).value),
            }
    except TrackingError as e:
        info["reason"] = str(e)
    except PermissionError:
        info["reason"] = f"{path.name}: {PRIVACY_HINT}"
    except OSError as e:
        info["reason"] = f"{path.name}: cannot be read ({e.strerror or e})"
    return info


def find_candidates(search_dirs: list[Path], blocked: list | None = None) -> list[dict]:
    """Workbooks that have the tracking sheet + table. The user picks one.

    Folders macOS privacy settings refuse to list are appended to `blocked`.
    """
    found, examined = [], 0
    blocked = blocked if blocked is not None else []

    def on_error(err: OSError) -> None:
        if isinstance(err, PermissionError) and err.filename:
            blocked.append(str(err.filename))

    for root in search_dirs:
        root = Path(root).expanduser()
        try:
            if not root.is_dir():
                continue
        except PermissionError:
            blocked.append(str(root))
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
            depth = len(Path(dirpath).parts) - base_depth
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SEARCH_SKIP_DIRS
                           and depth < SEARCH_MAX_DEPTH]
            for name in filenames:
                if not name.lower().endswith(".xlsx") or name.startswith(("~$", ".")):
                    continue
                p = Path(dirpath) / name
                try:
                    if p.stat().st_size > 20 * 1024 * 1024:
                        continue
                except OSError:
                    continue
                examined += 1
                if examined > SEARCH_MAX_FILES:
                    return found
                info = describe(p)
                if info["valid"]:
                    info.pop("emails", None)
                    found.append(info)
    return sorted(found, key=lambda i: i.get("mtime", ""), reverse=True)


# --------------------------------------------------------------------------- snapshot / verify


def _style_sig(c):
    f, fill, a = c.font, c.fill, c.alignment
    return (f.name, f.sz, f.b, f.i, f.u, getattr(f.color, "rgb", None), fill.fill_type,
            getattr(fill.fgColor, "rgb", None), c.number_format, a.horizontal, a.vertical, a.wrap_text,
            *(getattr(getattr(c.border, side, None), "style", None)
              for side in ("left", "right", "top", "bottom")),
            getattr(c.protection, "locked", None))


def _snapshot(wb) -> dict:
    snap = {"sheets": list(wb.sheetnames), "cells": {}, "styles": {}, "tables": {}, "dv": {}, "cf": {},
            "widths": {}, "merged": {}, "heights": {}, "freeze": {}}
    for ws in wb.worksheets:
        t = ws.title
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None:
                    snap["cells"][(t, c.coordinate)] = _as_date(c.value)
                if c.has_style:
                    snap["styles"][(t, c.coordinate)] = _style_sig(c)
        for name, tbl in dict.items(ws.tables):  # TableList.items() yields refs, not tables
            snap["tables"][(t, name)] = (tbl.ref, [col.name for col in tbl.tableColumns],
                                         tbl.tableStyleInfo.name if tbl.tableStyleInfo else None)
        snap["dv"][t] = sorted((str(d.sqref), d.type, d.formula1, d.allow_blank)
                               for d in ws.data_validations.dataValidation)
        snap["cf"][t] = sorted((str(r.sqref), len(r.rules)) for r in ws.conditional_formatting)
        snap["widths"][t] = {k: v.width for k, v in ws.column_dimensions.items() if v.width}
        snap["merged"][t] = sorted(str(m) for m in ws.merged_cells.ranges)
        snap["heights"][t] = {k: v.height for k, v in ws.row_dimensions.items() if v.height}
        snap["freeze"][t] = ws.freeze_panes
    return snap


def _verify(before: dict, after: dict, changed: dict, expect_tables: dict, expect_dv: dict,
            expect_widths: dict) -> None:
    """Raise TrackingError unless `after` == `before` + exactly the intended changes."""
    problems = []
    if before["sheets"] != after["sheets"]:
        problems.append("sheet list changed")
    for key, val in before["cells"].items():
        if key in changed:
            continue
        if after["cells"].get(key) != val:
            problems.append(f"existing cell {key[0]}!{key[1]} changed")
    for key, val in before["styles"].items():
        if key not in changed and after["styles"].get(key) != val:
            problems.append(f"formatting of {key[0]}!{key[1]} changed")
    for key, val in changed.items():
        if _as_date(after["cells"].get(key)) != (_as_date(val) if val not in ("", None) else None):
            problems.append(f"cell {key[0]}!{key[1]} does not hold the intended value")
    for key in after["cells"]:
        if key not in before["cells"] and key not in changed:
            problems.append(f"unexpected new cell {key[0]}!{key[1]}")
    if after["tables"] != {**before["tables"], **expect_tables}:
        problems.append("table definition differs from expected")
    if after["dv"] != {**before["dv"], **expect_dv}:
        problems.append("dropdowns (data validation) differ from expected")
    for k in ("cf", "merged", "heights", "freeze"):
        if before[k] != after[k]:
            problems.append(f"{k} changed")
    for sheet in set(before["widths"]) | set(after["widths"]):
        if after["widths"].get(sheet) != {**before["widths"].get(sheet, {}), **expect_widths.get(sheet, {})}:
            problems.append(f"column widths on '{sheet}' changed")
    if problems:
        raise TrackingError("verification failed, original left untouched: " + "; ".join(problems[:5]))


# --------------------------------------------------------------------------- apply


def _plan_changes(wb, ops: list[RowOp], result: ApplyResult):
    """Mutate `wb` in memory. Returns (changed cells, expected tables, dv, widths)."""
    ws, table, last_row, has_pw = _locate_table(wb)
    t = ws.title
    changed: dict = {}
    expect_widths: dict = {}

    def put(r: int, col: int, value):
        cell = ws.cell(r, col)
        cell.value = value
        changed[(t, cell.coordinate)] = value

    if not has_pw:
        pw_col = COL["password"]
        for r in range(1, max(ws.max_row, last_row) + 1):
            if not _blank(ws.cell(r, pw_col).value):
                raise TrackingError(f"column G is not empty (G{r}); cannot add the {PASSWORD_HEADER} column")
        hdr = ws.cell(1, pw_col)
        hdr._style = copy(ws.cell(1, COL["note"])._style)
        put(1, pw_col, PASSWORD_HEADER)
        table.tableColumns.append(TableColumn(id=max(c.id for c in table.tableColumns) + 1, name=PASSWORD_HEADER))
        letter = get_column_letter(pw_col)
        # Membership test first: indexing column_dimensions creates a default entry.
        if letter not in ws.column_dimensions or not ws.column_dimensions[letter].customWidth:
            ws.column_dimensions[letter].width = PASSWORD_COLUMN_WIDTH
            expect_widths.setdefault(t, {})[letter] = PASSWORD_COLUMN_WIDTH
        result.password_column_added = True

    rows = _email_rows(ws, last_row)
    template_row = last_row if last_row >= 2 else None
    end_row = last_row

    def append_row(values: dict[int, object]) -> None:
        nonlocal end_row
        r = end_row + 1
        for c in range(1, COL["password"] + 1):
            if not _blank(ws.cell(r, c).value):
                raise TrackingError(f"row {r} below the table is not empty; cannot append")
        for c in range(1, COL["password"] + 1):
            cell = ws.cell(r, c)
            if template_row:
                src_col = c if c != COL["password"] or has_pw else COL["note"]
                cell._style = copy(ws.cell(template_row, src_col)._style)
            if c in values and not _blank(values[c]):
                put(r, c, values[c])
            if c == COL["date"] and c in values:
                cell.number_format = DATE_FORMAT
        end_row = r

    for op in ops:
        email = _norm(op.email)
        found = rows.get(email, [])
        if len(found) > 1:
            result.conflicts.append((email, f"appears in {len(found)} rows ({', '.join(map(str, found))})"))
            continue

        if op.kind in ("create", "import"):
            if not found:
                append_row({COL["email"]: email, COL["status"]: STATUS_NEW, COL["date"]: op.date,
                            COL["note"]: NOTE_CREATED, COL["password"]: op.password})
                rows[email] = [end_row]
                result.added.append(email)
                continue
            r = found[0]
            current_pw = ws.cell(r, COL["password"]).value
            if not _blank(current_pw) and str(current_pw) != op.password:
                result.conflicts.append((email, f"row {r} already has a different password; not overwritten"))
                continue
            touched = False
            for col, value in ((COL["password"], op.password), (COL["status"], STATUS_NEW),
                               (COL["date"], op.date), (COL["note"], NOTE_CREATED)):
                if value is not None and _blank(ws.cell(r, col).value):
                    put(r, col, value)
                    if col == COL["date"]:
                        ws.cell(r, col).number_format = DATE_FORMAT
                    touched = True
            (result.updated if touched else result.unchanged).append(email)

        elif op.kind == "reset":
            if not found:
                append_row({COL["email"]: email, COL["note"]: NOTE_RESET, COL["password"]: op.password})
                rows[email] = [end_row]
                result.added.append(email)
            else:
                put(found[0], COL["password"], op.password)
                result.updated.append(email)
        else:
            raise ValueError(f"unknown op {op.kind}")

    table.ref = f"A1:{get_column_letter(COL['password'])}{end_row}"
    expect_tables = {(t, TABLE): (table.ref, [c.name for c in table.tableColumns],
                                  table.tableStyleInfo.name if table.tableStyleInfo else None)}

    # Extend dropdowns that covered the old last table row, if the table grew past them.
    for dv in ws.data_validations.dataValidation:
        new_ranges = []
        for rng in str(dv.sqref).split():
            c1, r1, c2, r2 = range_boundaries(rng)
            if r1 <= last_row <= r2 and end_row > r2:
                rng = f"{get_column_letter(c1)}{r1}:{get_column_letter(c2)}{end_row}"
            new_ranges.append(rng)
        dv.sqref = " ".join(new_ranges)
    expect_dv = {t: sorted((str(d.sqref), d.type, d.formula1, d.allow_blank)
                           for d in ws.data_validations.dataValidation)}
    return changed, expect_tables, expect_dv, expect_widths


def _backup(path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = backup_dir / f"{path.stem}.{stamp}{path.suffix}"
    shutil.copy2(path, dest)
    os.chmod(dest, 0o600)
    olds = sorted(backup_dir.glob(f"{path.stem}.*{path.suffix}"))
    for old in olds[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return dest


def _fsync_dir(d: Path) -> None:
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def apply(path: Path, ops: list[RowOp], backup_dir: Path, lock_path: Path) -> ApplyResult:
    """Apply `ops` to the workbook safely. Raises TrackingError if nothing was written."""
    path = Path(path).expanduser().resolve()
    result = ApplyResult()
    with _exclusive(lock_path):
        if excel_lock_files(path):
            raise ExcelOpenError(f"{path.name} is open in Excel. Close it, then retry.")
        before_fp = fingerprint(path)
        original = _load(path)
        before = _snapshot(original)
        wb = _load(path)
        changed, expect_tables, expect_dv, expect_widths = _plan_changes(wb, ops, result)
        if not result.written:
            return result

        result.backup = _backup(path, backup_dir)
        fd, tmp_name = tempfile.mkstemp(prefix=".~bgyhub-tmp-", suffix=".xlsx", dir=path.parent)
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            wb.save(tmp)
            with open(tmp, "rb+") as fh:
                os.fsync(fh.fileno())
            _verify(before, _snapshot(_load(tmp)), changed, expect_tables, expect_dv, expect_widths)
            if fingerprint(path) != before_fp:
                raise TrackingError(f"{path.name} was modified by something else during the update; retry")
            if excel_lock_files(path):
                raise ExcelOpenError(f"{path.name} was opened in Excel during the update. Close it, then retry.")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            _fsync_dir(path.parent)
        finally:
            tmp.unlink(missing_ok=True)
    return result
