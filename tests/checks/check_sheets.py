"""Offline checks for the Sheets layer.

Drives SheetsClient against an in-memory fake workbook, so it asserts dtype
coercion, empty-tab handling, the write guard, API-call batching, and
validate_schema without touching the network or the read quota.

Run: python tests/test_sheets.py
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import pandas as pd
from datetime import datetime

import finance_app.data.sheets as S
from finance_app.data.models import WishlistItem, SCHEMA, REPORT_TAB

CALLS = []
LAST_BATCH = []

class FakeWS:
    def __init__(self, title, values): self.title, self.values = title, values
    def get_all_values(self):
        CALLS.append(("get_all_values", self.title)); return [list(r) for r in self.values]
    def col_values(self, n):
        CALLS.append(("col_values", self.title, n))
        return [ (r[n-1] if n-1 < len(r) else "") for r in self.values ]
    def row_values(self, n):
        CALLS.append(("row_values", self.title, n))
        return list(self.values[n-1]) if n-1 < len(self.values) else []
    def append_rows(self, rows, value_input_option=None):
        CALLS.append(("append_rows", self.title, len(rows))); self.values += [list(r) for r in rows]
    def update_cell(self, r, c, v):
        CALLS.append(("update_cell", self.title, r, c)); self.values[r-1][c-1] = v
    def batch_update(self, batch, value_input_option=None):
        CALLS.append(("batch_update", self.title, len(batch))); LAST_BATCH.append(batch)

class FakeSS:
    def __init__(self, tabs): self.tabs = tabs
    def worksheet(self, t):
        if t not in self.tabs:
            import gspread; raise gspread.WorksheetNotFound(t)
        return self.tabs[t]
    def worksheets(self): return list(self.tabs.values())
    def values_batch_get(self, ranges):
        CALLS.append(("values_batch_get", len(ranges)))
        out=[]
        for r in ranges:
            tab = r.split("!")[0].strip("'")
            out.append({"values":[self.tabs[tab].values[0]]} if self.tabs[tab].values else {})
        return {"valueRanges": out}

def hdr(tab): return [c.header for c in SCHEMA[tab]]

tabs = {t: FakeWS(t, [hdr(t)]) for t in SCHEMA}
tabs[REPORT_TAB] = FakeWS(REPORT_TAB, [["", "Label", "Value"]])
# populate a few
tabs["_Accounts"].values += [
    ["a1","Checking","checking","Chase","$1,875.95","USD","2026-01-05"],
    ["a2","Savings","savings","Ally","(250.00)","USD","2026-02-11"],
]
tabs["_Transactions"].values += [
    ["t1","2026-03-01","a1","Coffee","Food","-4.50",""],
    ["","","","","","",""],   # blank filler row
]
tabs["_Debts"].values += [["d1","Card","credit","$4,200.10","19.99%","$75.00","15","a1"]]
tabs["_Config"].values += [["emergency_buffer","1000"],["  ",""],["currency","USD"]]

ss = FakeSS(tabs)
class C: google_creds_path="x"; google_sheet_id="sid"
client = S.SheetsClient.__new__(S.SheetsClient); client._settings = C()
S.SheetsClient.spreadsheet = property(lambda self: ss)

print("=== dtypes / currency parsing ===")
acc = client.get_accounts()
print(acc[["Account ID","Balance","Last Updated"]].to_string(index=False))
print("Balance dtype:", acc["Balance"].dtype, "| Last Updated:", acc["Last Updated"].dtype)
assert acc["Balance"].tolist() == [1875.95, -250.0], acc["Balance"].tolist()

d = client.get_debts()
print("APR:", d["APR"].tolist(), "| Due Day dtype:", d["Due Day"].dtype)
assert d["APR"].tolist() == [19.99] and d["Minimum Payment"].tolist() == [75.0]

tx = client.get_transactions()
print("txn rows (blank dropped):", len(tx), "| Amount:", tx["Amount"].tolist())
assert len(tx) == 1

print("\n=== empty tabs (headers only) ===")
for name, fn in [("_Recurring", client.get_recurring), ("_Budgets", client.get_budgets),
                 ("_Allocations", client.get_allocations), ("_Wishlist", client.get_wishlist)]:
    f = fn()
    assert list(f.columns) == hdr(name), (name, list(f.columns))
    assert f.empty
    print(f"{name:14} empty={f.empty} cols={len(f.columns)} dtypes ok")

print("\n=== get_config -> dict ===")
cfg = client.get_config()
print(cfg); assert cfg == {"emergency_buffer":"1000","currency":"USD"}, cfg

print("\n=== write guard ===")
for bad in [REPORT_TAB, "Sheet1", "Dashboard"]:
    try:
        S._guard_write(bad); print("FAIL no raise", bad)
    except S.SheetsError as e:
        print(f"  blocked {bad!r}: {str(e)[:58]}...")

print("\n=== writes are batched ===")
CALLS.clear()
n = client.append_transactions(pd.DataFrame([
    {"Date":"2026-03-02","Account ID":"a1","Description":"Book","Category":"Fun","Amount":"$12.99"},
    {"Date":"2026-03-03","Account ID":"a1","Description":"Gas","Category":"Auto","Amount":"-40"},
]))
print("appended", n, "rows via calls:", CALLS)
assert [c[0] for c in CALLS].count("append_rows") == 1, "must batch into ONE call"
print("generated IDs:", [r[0] for r in tabs["_Transactions"].values[-2:]])
assert all(r[0] for r in tabs["_Transactions"].values[-2:])

CALLS.clear()
iid = client.append_wishlist_item(WishlistItem(item_id="", name="Desk", price=299.0))
print("wishlist id:", iid, "| calls:", CALLS)
CALLS.clear()
client.update_wishlist_status(iid, "purchased")
print("status update calls:", CALLS)
assert [c[0] for c in CALLS] == ["get_all_values","batch_update"]

CALLS.clear()
client.update_account_balance("a1", 2000.0, datetime(2026,3,4,10,0,0))
print("balance update calls:", CALLS, "(2 cells, 1 write call)")
assert CALLS[-1][0] == "batch_update" and CALLS[-1][2] == 2

CALLS.clear()
bid = client.upsert_budget("2026-03","Food", 400.0)
print("insert ->", bid, CALLS)
CALLS.clear()
bid2 = client.upsert_budget("2026-03","Food", 450.0)
print("update ->", bid2, CALLS)
assert bid == bid2, "upsert must reuse the row"
assert [c[0] for c in CALLS] == ["get_all_values","update_cell"]

print("\n=== missing row raises named error ===")
try: client.update_wishlist_status("nope","x")
except S.SheetsError as e: print("  ", e)

print("\n=== reordered columns must not corrupt writes ===")
tabs["_Accounts"].values[0] = ["Account ID","Name","Type","Institution","Currency","Balance","Last Updated"]
tabs["_Accounts"].values[1] = ["a1","Checking","checking","Chase","USD","1875.95","2026-01-05"]
LAST_BATCH.clear()
client.update_account_balance("a1", 3333.0)
print("  ranges written:", [b["range"] for b in LAST_BATCH[-1]])
assert LAST_BATCH[-1][0]["range"].startswith("F"), LAST_BATCH[-1]
print("  -> Balance hit column F (schema order alone would have said E)")
tabs["_Accounts"].values[0] = hdr("_Accounts")
tabs["_Accounts"].values[1] = ["a1","Checking","checking","Chase","$1,875.95","USD","2026-01-05"]

print("\n=== dtype consistency: empty vs populated ===")
pop = client.get_debts()["Balance"].dtype
emp = S._frame([hdr("_Debts")], SCHEMA["_Debts"])["Balance"].dtype
print("  money  populated/empty:", pop, "/", emp); assert pop == emp
popd = client.get_transactions()["Date"].dtype
empd = S._frame([hdr("_Transactions")], SCHEMA["_Transactions"])["Date"].dtype
print("  date   populated/empty:", popd, "/", empd); assert popd == empd, (popd, empd)

print("\n=== validate_schema ===")
CALLS.clear()
print("clean:", S.validate_schema(client), "| API calls:", CALLS)
tabs["_Debts"].values[0] = ["Debt ID","Name","Type","Balance","Interest","Minimum Payment","Due Day","Account ID"]
del tabs["_Allocations"]
print("broken:", S.validate_schema(client))
tabs["_Debts"].values[0] = ["Debt ID","Name","Type","Balance","APR","Minimum Payment","Due Day","Account ID"]
# A reordered tab is no longer a problem: reads resolve by header and appends
# align to the live header, so order cannot shift a value one column across.
_swapped = hdr("_Wishlist")[:]
_swapped[0], _swapped[1] = _swapped[1], _swapped[0]
tabs["_Wishlist"].values[0] = _swapped
probs = S.validate_schema(client)
print("reordered _Wishlist:", [p for p in probs if "_Wishlist" in p] or "accepted")
assert not [p for p in probs if "_Wishlist" in p], probs
tabs["_Wishlist"].values[0] = hdr("_Wishlist")

print("\n=== appends land by header, not by schema position ===")
# Transaction ID last and Amount before Description: nothing like schema order.
tabs["_Transactions"] = FakeWS("_Transactions", [
    ["Date","Amount","Description","Category","Account ID","Notes","Transaction ID"]
])
client.append_transactions(pd.DataFrame([
    {"Date":"2026-03-05","Account ID":"a9","Description":"Tea","Category":"Food","Amount":"-3.25"},
]))
written = tabs["_Transactions"].values[-1]
print("  row written:", written)
assert written[0] == "2026-03-05" and written[1] == -3.25, written
assert written[2] == "Tea" and written[4] == "a9", written
assert written[6], "Transaction ID should be generated into the last column"
print("  -> each value under its own header; schema order would have scrambled this")

print("\n=== missing tab error names it ===")
try: client.get_allocations()
except S.SheetsError as e: print("  ", e)

print("\nALL ASSERTIONS PASSED")

print("\n=== batched budget save: one read, at most two writes ===")
tabs["_Budgets"].values = [hdr("_Budgets"),
                           ["b1","2026-09","Groceries","300",""],
                           ["b2","2026-09","Dining","100",""]]
CALLS.clear()
n = client.upsert_budgets("2026-09", pd.DataFrame([
    {"Category":"Groceries","Amount":"$350.00"},   # update
    {"Category":"Dining",   "Amount":120},          # update
    {"Category":"Gas",      "Amount":80},           # insert
    {"Category":"Health",   "Amount":45},           # insert
]))
print("  rows saved:", n, "| calls:", CALLS)
kinds = [c[0] for c in CALLS]
assert kinds == ["get_all_values","batch_update","append_rows"], kinds
assert kinds.count("batch_update") == 1 and kinds.count("append_rows") == 1
print("  4 categories -> 3 API calls total (not 8)")
assert LAST_BATCH[-1][0]["values"] == [[350.0]], LAST_BATCH[-1]
print("  currency string '$350.00' parsed to 350.0 before writing")

print("\n=== set_config inserts then updates ===")
CALLS.clear()
client.set_config("rollover_enabled", "true")
assert [c[0] for c in CALLS] == ["get_all_values","append_rows"], CALLS
client.set_config("rollover_enabled", "false")
assert client.get_config()["rollover_enabled"] == "false"
print("  key round-trips:", client.get_config()["rollover_enabled"])

print("\n=== new writers respect the underscore guard ===")
import finance_app.data.sheets as _S
for name, args in [("upsert_budgets", ("2026-09", pd.DataFrame([{"Category":"X","Amount":1}]))),
                   ("add_allocation", ("2026-09","x",1.0))]:
    pass
saved = _S.Budget.TAB
try:
    _S.Budget.TAB = "Budget Sheet"
    try:
        client.upsert_budgets("2026-09", pd.DataFrame([{"Category":"X","Amount":1}]))
        print("  FAIL: wrote to the report tab")
    except _S.SheetsError as e:
        print("  blocked:", str(e)[:56] + "...")
finally:
    _S.Budget.TAB = saved

print("\n=== goal / debt / allocation writers ===")
tabs["_Goals"].values = [hdr("_Goals"),
    ["g1","Emergency","10000","4000","2027-09-01","emergency","a2",""]]
CALLS.clear()
client.update_goal_saved("g1", 4500.0)
print("  goal:", CALLS)
assert [c[0] for c in CALLS] == ["get_all_values","batch_update"]
tabs["_Debts"].values = [hdr("_Debts"), ["d1","Visa","credit","1500","19.99","75","15","a3"]]
CALLS.clear(); client.update_debt_balance("d1", 1200.0)
assert [c[0] for c in CALLS] == ["get_all_values","batch_update"]
tabs["_Allocations"] = FakeWS("_Allocations", [hdr("_Allocations")])  # restored: an earlier check deleted it
CALLS.clear(); aid = client.add_allocation("2026-09", "emergency", 500.0, "a2")
print("  allocation id:", aid, CALLS)
assert [c[0] for c in CALLS] == ["append_rows"]

print("\nEXTENDED WRITER ASSERTIONS PASSED")

print("\n=== update_transactions: many rows, one write ===")
tabs["_Transactions"] = FakeWS("_Transactions", [hdr("_Transactions"),
    ["t1","2026-09-01","a1","Coffee","","-4.50",""],
    ["t2","2026-09-02","a1","Market","","-200.00",""],
    ["t3","2026-09-03","a1","Rent","Rent","-1000.00",""],
])
CALLS.clear(); LAST_BATCH.clear()
n = client.update_transactions({
    "t1": {"Category": "Dining"},
    "t2": {"Category": "Groceries", "Notes": "weekly shop"},
})
kinds = [c[0] for c in CALLS]
print("  rows updated:", n, "| calls:", CALLS)
assert kinds == ["get_all_values", "batch_update"], kinds
assert n == 2 and CALLS[-1][2] == 3, "2 rows / 3 cells must go out in one call"
print("  2 rows, 3 cells -> 2 API calls (per-row updates would have cost 4)")

print("\n=== only Category, Notes and Account ID are editable ===")
for field in ("Amount", "Date", "Description", "Transaction ID"):
    try:
        client.update_transactions({"t1": {field: "tampered"}})
        print("  FAIL: allowed an edit to", field)
    except S.SheetsError as e:
        assert field in str(e), e
print("  refused: Amount, Date, Description, Transaction ID")

print("\n=== an unknown ID aborts before anything is written ===")
CALLS.clear()
try:
    client.update_transactions({"t1": {"Category": "Dining"}, "nope": {"Category": "X"}})
    print("  FAIL: no raise")
except S.SheetsError as e:
    print("  ", str(e)[:74] + "...")
assert [c[0] for c in CALLS] == ["get_all_values"], CALLS
print("  nothing written: the good edit was not half-applied")

print("\n=== update_transactions survives reordered columns ===")
tabs["_Transactions"].values[0] = ["Transaction ID","Date","Account ID","Description","Amount","Category","Notes"]
tabs["_Transactions"].values[1] = ["t1","2026-09-01","a1","Coffee","-4.50","",""]
LAST_BATCH.clear()
client.update_transactions({"t1": {"Category": "Dining"}})
print("  range written:", LAST_BATCH[-1][0]["range"])
assert LAST_BATCH[-1][0]["range"].startswith("F"), LAST_BATCH[-1]
print("  -> Category hit column F (schema order alone would have said E)")

print("\n=== empty change set costs no API calls ===")
CALLS.clear()
assert client.update_transactions({}) == 0
assert CALLS == [], CALLS
print("  no calls made")

print("\nTRANSACTION WRITER ASSERTIONS PASSED")
