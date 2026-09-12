"""Create any missing data tabs in the workbook, with their header rows.

Idempotent and conservative:

- Creates only ``_``-prefixed data tabs, driven by ``models.SCHEMA`` so the
  headers can never drift from what the app reads.
- A tab that already exists is left alone apart from **appending** headers it is
  missing, as new trailing columns. Existing columns are never reordered,
  renamed, or removed, and no data row is ever touched.
- ``Budget Sheet`` is never created or written to — it is your report.

Usage::

    python scripts/bootstrap_sheet.py --dry-run     # show the plan, change nothing
    python scripts/bootstrap_sheet.py               # apply it (asks first)
    python scripts/bootstrap_sheet.py --yes         # apply without prompting
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gspread  # noqa: E402

from finance_app.config import ConfigError, get_config  # noqa: E402
from finance_app.data.models import REPORT_TAB, SCHEMA  # noqa: E402
from finance_app.data.sheets import SheetsError, _open_spreadsheet  # noqa: E402

#: Rows and columns given to a freshly created tab.
NEW_TAB_ROWS = 1000


class Plan:
    """What bootstrapping would do, worked out before anything is written."""

    def __init__(self) -> None:
        self.create: list[tuple[str, list[str]]] = []
        self.add_columns: list[tuple[str, list[str]]] = []
        self.conflicts: list[tuple[str, list[str], list[str]]] = []
        self.ok: list[str] = []
        self.notes: list[str] = []

    @property
    def empty(self) -> bool:
        """True when there is nothing this script can safely do."""
        return not self.create and not self.add_columns

    def render(self) -> None:
        """Print the plan, one line per tab."""
        for tab, headers in self.create:
            print(f"  {tab:16} CREATE  ({len(headers)} headers)")
        for tab, missing in self.add_columns:
            names = ", ".join(repr(name) for name in missing)
            print(f"  {tab:16} exists, ADD COLUMN {names}")
        for tab, actual, _ in self.conflicts:
            print(f"  {tab:16} SKIP — row 1 is a different convention: "
                  + ", ".join(repr(name) for name in actual[:4])
                  + ("…" if len(actual) > 4 else ""))
        for tab in self.ok:
            print(f"  {tab:16} exists, headers OK")
        for note in self.notes:
            print(f"  {note}")
        total = len(SCHEMA)
        print(
            f"\n  {total} tab(s) checked / {len(self.create)} to create / "
            f"{len(self.add_columns)} to extend / {len(self.conflicts)} skipped / "
            "0 rows touched"
        )

    def render_conflicts(self) -> None:
        """Explain the skipped tabs and what the choice is."""
        if not self.conflicts:
            return
        print(
            f"\n{len(self.conflicts)} tab(s) were skipped. Each already has a "
            "header row that shares no column name with the schema — almost "
            "always the same fields under a different naming convention, with "
            "real data underneath.\n"
            "\nAppending the schema's names as new columns would leave the tab "
            "with two header sets and the app reading the empty half, so this "
            "script will not do it. Rename row 1 in place to the names below "
            "(the data underneath keeps its columns), then run this again."
        )
        for tab, actual, expected in self.conflicts:
            print(f"\n  {tab}")
            print(f"    row 1 now: {', '.join(actual)}")
            print(f"    schema:    {', '.join(expected)}")


def build_plan(spreadsheet) -> Plan:
    """Compare the live workbook against the schema. Reads only."""
    plan = Plan()
    existing = {ws.title: ws for ws in spreadsheet.worksheets()}

    for tab, columns in SCHEMA.items():
        expected = [column.header for column in columns]
        if tab not in existing:
            plan.create.append((tab, expected))
            continue

        row = existing[tab].row_values(1) if existing[tab].row_count else []
        actual = [str(cell).strip() for cell in row]
        present = [name for name in actual if name]
        missing = [name for name in expected if name not in actual]

        # A populated row 1 that matches nothing is a different naming
        # convention, not a set of missing columns. Appending here would give
        # the tab two header sets and leave the app reading the empty one, so
        # it is reported for a human to rename instead.
        if present and len(missing) == len(expected):
            plan.conflicts.append((tab, present, expected))
        elif missing:
            plan.add_columns.append((tab, missing))
        else:
            plan.ok.append(tab)

    if REPORT_TAB in existing:
        plan.notes.append(f"{REPORT_TAB:16} skipped (never written to)")
    else:
        plan.notes.append(
            f"{REPORT_TAB:16} not present — optional, and the app never writes it"
        )
    return plan


def apply(spreadsheet, plan: Plan) -> None:
    """Carry out the plan. Only ever adds tabs and header cells."""
    for tab, headers in plan.create:
        worksheet = spreadsheet.add_worksheet(
            title=tab, rows=NEW_TAB_ROWS, cols=max(len(headers), 8)
        )
        worksheet.update([headers], "A1")
        worksheet.freeze(rows=1)
        print(f"  created {tab} with {len(headers)} headers")

    for tab, missing in plan.add_columns:
        worksheet = spreadsheet.worksheet(tab)
        actual = [str(cell).strip() for cell in worksheet.row_values(1)]
        start = len(actual) + 1
        if start + len(missing) - 1 > worksheet.col_count:
            worksheet.add_cols(start + len(missing) - 1 - worksheet.col_count)
        cell = gspread.utils.rowcol_to_a1(1, start)
        worksheet.update([missing], f"{cell}:{gspread.utils.rowcol_to_a1(1, start + len(missing) - 1)}")
        print(f"  extended {tab} with {', '.join(missing)}")


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show the plan only")
    parser.add_argument("--yes", action="store_true", help="apply without prompting")
    args = parser.parse_args()

    try:
        settings = get_config()
    except ConfigError as exc:
        print(f"Configuration problem:\n  {exc}", file=sys.stderr)
        return 2

    print(f"Sheet:      {settings.google_sheet_id}")
    print(f"Credentials {settings.creds_source}\n")

    try:
        spreadsheet = _open_spreadsheet(
            settings, settings.creds_source, settings.google_sheet_id
        )
        print(f"Opened {spreadsheet.title!r}\n")
        plan = build_plan(spreadsheet)
    except SheetsError as exc:
        print(f"Could not open the spreadsheet:\n  {exc}", file=sys.stderr)
        print(
            "\nCheck that the sheet is shared with your service account's email "
            "as an Editor, and that GOOGLE_SHEET_ID matches the sheet URL.",
            file=sys.stderr,
        )
        return 2

    plan.render()
    plan.render_conflicts()

    if plan.empty:
        if plan.conflicts:
            print("\nNothing was changed: every remaining tab needs the rename above.")
            return 3
        print("\nNothing to do — the workbook already matches the schema.")
        return 0
    if args.dry_run:
        print("\nDry run: nothing was changed.")
        return 0

    if not args.yes:
        answer = input("\nApply this plan? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled. Nothing was changed.")
            return 1

    print()
    try:
        apply(spreadsheet, plan)
    except gspread.exceptions.APIError as exc:
        # Opening the sheet only proves read access. Sharing it with the
        # service account as Viewer gets you all the way to here and then
        # fails on the first write, so say so rather than showing a traceback.
        if _status(exc) in (401, 403):
            print(f"\nPermission denied writing to {spreadsheet.title!r}.", file=sys.stderr)
            print(
                "The service account can read this sheet but not write to it, "
                "so it was shared as Viewer rather than Editor.\n\n"
                f"Open the sheet → Share → {_service_account_email(settings)} "
                "→ set to Editor → Send, then run this again. Applying the plan "
                "twice is safe: it only ever adds what is still missing.",
                file=sys.stderr,
            )
            return 2
        print(f"\nThe Sheets API rejected the change:\n  {exc}", file=sys.stderr)
        return 2

    print("\nDone. Re-run with --dry-run to confirm.")
    return 3 if plan.conflicts else 0


def _status(exc: gspread.exceptions.APIError) -> int | None:
    """HTTP status behind an APIError, or None if it cannot be read."""
    return getattr(getattr(exc, "response", None), "status_code", None)


def _service_account_email(settings) -> str:
    """The ``client_email`` from the credentials, for the Share dialog.

    This is the one piece of information the fix needs and the one nobody has
    to hand. An inline key already carries it; a key file that cannot be read
    falls back to a description rather than failing the error path itself.
    """
    if settings.google_creds_info is not None:
        return settings.google_creds_info.get("client_email", "") or "your service account"
    try:
        with open(settings.google_creds_path, encoding="utf-8") as handle:
            return json.load(handle).get("client_email", "") or "your service account"
    except Exception:  # noqa: BLE001 - this runs inside error reporting
        return (
            f"the client_email in {settings.google_creds_path}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
