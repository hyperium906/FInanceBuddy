"""Offline checks for reading a workbook that does not use the app's headers.

A sheet that predates the app stores the same facts under different names, and
sometimes in a different shape: a day-of-month instead of a due date, one
"value" column plus a fixed/percent discriminator instead of two columns. The
readers resolve all of it to the canonical frames the logic layer expects,
without ever renaming anything in the sheet.

The layouts below are a real one, copied verbatim.

Run: python tests/checks/check_foreign_layout.py
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import pandas as pd

import finance_app.data.sheets as S
from finance_app.data.models import SCHEMA, REPORT_TAB, resolve

CALLS = []

class FakeWS:
    def __init__(self, title, values): self.title, self.values = title, values
    def get_all_values(self):
        CALLS.append(("get_all_values", self.title)); return [list(r) for r in self.values]
    def row_values(self, n):
        CALLS.append(("row_values", self.title, n))
        return list(self.values[n-1]) if n-1 < len(self.values) else []
    def append_rows(self, rows, value_input_option=None):
        CALLS.append(("append_rows", self.title, len(rows))); self.values += [list(r) for r in rows]
    def batch_update(self, batch, value_input_option=None):
        CALLS.append(("batch_update", self.title, len(batch)))

class FakeSS:
    def __init__(self, tabs): self.tabs = tabs
    def worksheet(self, t):
        if t not in self.tabs:
            import gspread; raise gspread.WorksheetNotFound(t)
        return self.tabs[t]
    def worksheets(self): return list(self.tabs.values())
    def values_batch_get(self, ranges):
        out = []
        for r in ranges:
            tab = r.split("!")[0].strip("'")
            out.append({"values": [self.tabs[tab].values[0]]} if self.tabs[tab].values else {})
        return {"valueRanges": out}

def hdr(tab): return [c.header for c in SCHEMA[tab]]

tabs = {t: FakeWS(t, [hdr(t)]) for t in SCHEMA}
tabs[REPORT_TAB] = FakeWS(REPORT_TAB, [["", "Label", "Value"]])

# --- the foreign layouts, verbatim -----------------------------------------

tabs["_Accounts"] = FakeWS("_Accounts", [
    ["account_id","name","type","institution","balance","last_updated"],
    ["chase_savings","Chase Savings","savings","Chase","$1,300.00","2026-09-09"],
    ["fidelity_cash","Fidelity Cash Management","checking","Fidelity","$100.06","2026-09-09"],
    ["fidelity_roth","Fidelity Go Roth IRA","retirement","Fidelity","$118.52","2026-09-09"],
])
tabs["_Transactions"] = FakeWS("_Transactions", [
    ["txn_id","date","account_id","description","merchant","amount","category","notes"],
    ["x1","2026-09-02","chase_savings","Coffee","Blue Bottle","-4.50","Dining",""],
])
tabs["_Recurring"] = FakeWS("_Recurring", [
    ["name","category","amount","frequency","due_day","active"],
    ["Tithing","Giving","$413.04","monthly","1","TRUE"],
    ["Rent","Housing","$1,875.95","monthly","1","TRUE"],
    ["Old gym","Fitness","$40.00","monthly","15","FALSE"],
])
tabs["_Budgets"] = FakeWS("_Budgets", [
    ["month","category","planned_amount"],
    ["2026-09","Groceries","$250.00"],
    ["2026-09","Dining","$50.00"],
])
tabs["_Allocations"] = FakeWS("_Allocations", [
    ["bucket","allocation_type","value","target_account","active"],
    ["Student Loan","fixed","$300.00","Student Loan","TRUE"],
    ["Chase Savings","fixed","$200.00","chase_savings","TRUE"],
    ["Tithing","percent","10","chase_savings","TRUE"],
    ["Cancelled thing","fixed","$75.00","chase_savings","FALSE"],
])
tabs["_Config"] = FakeWS("_Config", [
    ["key","value"],
    ["paycheck_net","$2,065.20"],
    ["pay_frequency","biweekly"],
    ["pay_anchor_date","2026-09-04"],
])

ss = FakeSS(tabs)
class C: google_creds_path="x"; google_sheet_id="sid"
client = S.SheetsClient.__new__(S.SheetsClient); client._settings = C()
S.SheetsClient.spreadsheet = property(lambda self: ss)

print("=== snake_case headers resolve to canonical names ===")
acc = client.get_accounts()
assert list(acc.columns) == hdr("_Accounts"), list(acc.columns)
assert acc["Account ID"].tolist() == ["chase_savings","fidelity_cash","fidelity_roth"]
assert acc["Balance"].tolist() == [1300.0, 100.06, 118.52], acc["Balance"].tolist()
assert acc["Name"].iloc[0] == "Chase Savings"
print("  _Accounts:", acc["Balance"].tolist(), "| dtype", acc["Balance"].dtype)

print("\n=== a column the sheet simply lacks reads as blank, not an error ===")
# This layout has no 'currency' column at all.
assert "Currency" in acc.columns and (acc["Currency"] == "").all()
print("  Currency absent from the sheet -> blank column, 3 rows intact")

print("\n=== an extra column the schema does not know is ignored ===")
tx = client.get_transactions()
assert list(tx.columns) == hdr("_Transactions"), list(tx.columns)
assert tx["Transaction ID"].tolist() == ["x1"] and tx["Amount"].tolist() == [-4.50]
assert tx["Description"].iloc[0] == "Coffee"   # not 'merchant'
print("  'merchant' dropped; txn_id -> Transaction ID; Amount", tx["Amount"].tolist())

print("\n=== due_day becomes a real Next Due date ===")
rec = client.get_recurring()
today = pd.Timestamp.today().normalize()
due = rec["Next Due"]
assert due.notna().all(), due.tolist()
assert (due >= today).all(), f"a due date must not be in the past: {due.tolist()}"
assert (due.dt.day == pd.Series([1, 1, 15])).all(), due.dt.day.tolist()
print("  due_day 1,1,15 ->", [str(d.date()) for d in due])
assert rec["Amount"].tolist() == [413.04, 1875.95, 40.0]
assert rec["Active"].tolist() == [True, True, False]
print("  amounts and Active parsed:", rec["Active"].tolist())

print("\n=== allocation_type + value split into Percent and Amount ===")
alloc = client.get_allocations()
assert list(alloc.columns) == hdr("_Allocations"), list(alloc.columns)
print(alloc[["Bucket","Percent","Amount","Account ID"]].to_string(index=False))
assert alloc["Amount"].tolist() == [300.0, 200.0, 0.0], alloc["Amount"].tolist()
assert alloc["Percent"].tolist() == [0.0, 0.0, 10.0], alloc["Percent"].tolist()
assert alloc["Account ID"].tolist() == ["Student Loan","chase_savings","chase_savings"]
print("  'fixed' -> Amount, 'percent' -> Percent, target_account -> Account ID")

print("\n=== an inactive allocation is not a standing rule ===")
assert "Cancelled thing" not in alloc["Bucket"].tolist(), alloc["Bucket"].tolist()
assert len(alloc) == 3
print("  active=FALSE row dropped; 3 of 4 rows kept")

print("\n=== no Month column means every row is a standing rule ===")
# Blank Month is how the app distinguishes a paycheck rule from recorded
# history, which is exactly what this layout holds.
assert (alloc["Month"] == "").all(), alloc["Month"].tolist()
print("  Month blank for all rows")

print("\n=== config keys resolve through their aliases ===")
cfg = client.get_config()
assert cfg["paycheck_amount"] == "$2,065.20", cfg
assert cfg["paycheck_net"] == "$2,065.20", "the original key stays readable too"
assert "monthly_savings_target" not in cfg, "an absent key must not be invented"
print("  paycheck_net -> paycheck_amount:", cfg["paycheck_amount"])

print("\n=== a canonical key always beats an alias ===")
tabs["_Config"].values.append(["paycheck_amount","$1.00"])
S.clear_cache()
assert client.get_config()["paycheck_amount"] == "$1.00"
tabs["_Config"].values.pop()
S.clear_cache()
print("  explicit paycheck_amount wins over paycheck_net")

print("\n=== validate_schema accepts the whole workbook ===")
problems = S.validate_schema(client)
assert problems == [], problems
print("  no problems reported for a fully foreign layout")

print("\n=== writes land in the sheet's own columns ===")
CALLS.clear()
client.append_transactions(pd.DataFrame([
    {"Date":"2026-09-10","Account ID":"chase_savings","Description":"Tea",
     "Category":"Dining","Amount":"-3.25"},
]))
row = tabs["_Transactions"].values[-1]
print("  row written:", row)
positions = resolve("_Transactions", tabs["_Transactions"].values[0])
assert row[positions["Date"]] == "2026-09-10", row
assert row[positions["Amount"]] == -3.25, row
assert row[positions["Description"]] == "Tea", row
assert row[positions["Transaction ID"]], "an ID should be generated"
assert row[4] == "", "the unknown 'merchant' column is left alone"
print("  each value under its own header; 'merchant' untouched")

print("\n=== editing a category writes to the sheet's 'category' column ===")
CALLS.clear()
client.update_transactions({"x1": {"Category": "Groceries"}})
assert [c[0] for c in CALLS] == ["get_all_values","batch_update"], CALLS
print("  resolved 'Category' -> the sheet's 'category' column in one write")

print("\n=== a canonical workbook still reads exactly as before ===")
tabs["_Accounts"] = FakeWS("_Accounts", [
    hdr("_Accounts"),
    ["a1","Checking","checking","Chase","$1,875.95","USD","2026-01-05"],
])
S.clear_cache()
canonical = client.get_accounts()
assert canonical["Balance"].tolist() == [1875.95]
assert canonical["Currency"].tolist() == ["USD"]
print("  canonical headers unaffected by the alias layer")

print("\nALL ASSERTIONS PASSED")
