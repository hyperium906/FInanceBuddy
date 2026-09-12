"""Offline checks for categorization and the LLM boundary.

Runs the two tiers against a fake provider, so it asserts merchant scrubbing,
rule matching, batching, 429 backoff, and partial-failure tolerance without an
API key or a network call.

Run: python tests/test_categorize.py
"""
import sys, pathlib, re, json, time, shutil, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import os
os.environ.setdefault("GOOGLE_SHEET_ID", "test")
os.environ.setdefault("GOOGLE_CREDS_PATH", "test.json")
os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("GEMINI_MODEL", "test-pro")
os.environ.setdefault("GEMINI_MODEL_FAST", "test-flash")

import pandas as pd
from finance_app.logic import categorize as C
from finance_app import llm

print("=== scrubbing: nothing identifying may survive ===")
CASES = [
    "POS DEBIT TRADER JOE'S #417 XXXXXX4412 SAN JOSE CA",
    "ACH PMT COMCAST CABLE REF#8837261923 ACCT 000123456789",
    "SQ *BLUE BOTTLE COFFEE 4/12 AUTH 449102",
    "AMAZON.COM*2H4KL9DQ3 AMZN.COM/BILL WA",
    "VISA DDA PUR CHEVRON 0093312 TRACE 99182773 06/14",
    "PAYPAL *NETFLIX.COM 4029357733",
]
BAD = re.compile(r"\d{4,}|[x*]{3,}|\b(?:acct|auth|ref|trace|account)\b", re.I)
for raw in CASES:
    s = C.scrub_merchant(raw)
    leak = BAD.search(s)
    print(f"  {raw[:46]:46} -> {s!r}")
    assert not leak, f"LEAKED {leak.group()!r} in {s!r}"
print("  no 4+ digit runs or masked numbers survived")

print("\n=== scrubber must not eat merchant names ===")
for word, keep in [("EXXON MOBIL 4411", "exxon"), ("TEXACO STATION", "texaco"),
                   ("TJ MAXX 8812", "maxx"), ("H-E-B #221 AUSTIN TX", "h-e-b"),
                   ("7-ELEVEN 33812", "7-eleven")]:
    got = C.scrub_merchant(word)
    print(f"  {word:24} -> {got!r}")
    assert keep in got, (word, got)
for masked in ["TRADER JOE'S XXXX4412", "STORE ****5678", "CARD XXXXXXXXXXXX9012"]:
    assert not BAD.search(C.scrub_merchant(masked)), masked
print("  merchant text kept, masked numbers removed")

print("\n=== rule patterns match whole tokens only ===")
rr = C.load_rules()
for raw, want in [("PARENT TEACHER ASSOC", None), ("MONTHLY RENT PAYMENT", "Housing"),
                  ("BPOST INTERNATIONAL", None), ("BP STATION 4411", "Gas / Transportation"),
                  ("METROPOLITAN MUSEUM", None), ("METRO TRANSIT", "Gas / Transportation"),
                  ("APPLE.COM/BILL", "Subscriptions")]:
    got = C.apply_rules(C.scrub_merchant(raw), rr)
    print(f"  {raw:24} -> {got}")
    assert got == want, (raw, got, want)
print("  no substring false positives")

print("\n=== every starter rule survives its own scrubber ===")
import json as _json
bad = [r for r in _json.load(open("finance_app/category_rules.json"))["rules"]
       if C.scrub_merchant(r["match"]) != r["match"].strip().lower()]
assert not bad, bad
print("  all 95 starter rules are reachable")

print("\n=== tier 1: rules ===")
rules = C.load_rules()
for raw, want in [
    ("POS DEBIT TRADER JOE'S #417 XXXXXX4412 SAN JOSE CA", "Groceries"),
    ("ACH PMT COMCAST CABLE REF#8837261923", "Utilities"),
    ("PAYPAL *NETFLIX.COM 4029357733", "Subscriptions"),
    ("VISA DDA PUR CHEVRON 0093312", "Gas / Transportation"),
    ("DIRECT DEP ACME CORP PAYROLL", "Income"),
    ("UBER TRIP HELP.UBER.COM", "Gas / Transportation"),
]:
    got = C.apply_rules(C.scrub_merchant(raw), rules)
    print(f"  {C.scrub_merchant(raw)[:34]:34} -> {got}")
    assert got == want, (raw, got, want)

print("\n=== longest-match-wins ===")
rs = [C.Rule("uber", "Gas / Transportation"), C.Rule("uber eats", "Dining")]
rs.sort(key=lambda r: len(r.match), reverse=True)
assert C.apply_rules("uber eats order", rs) == "Dining"
assert C.apply_rules("uber trip", rs) == "Gas / Transportation"
print("  'uber eats' -> Dining, 'uber trip' -> Transport")

print("\n=== tier 2: batching + payload privacy ===")
SEEN = []
def fake_generate(prompt, schema=None, model=None, **kw):
    SEEN.append((prompt, schema, model))
    idxs = [int(m) for m in re.findall(r"^(\d+)\. merchant=", prompt, re.M)]
    return [{"index": i, "category": "Shopping"} for i in idxs]
C.generate = fake_generate

df = pd.DataFrame([
    {"Description": f"WEIRDMART OUTLET {i} ACCT 000111222{i}", "Amount": -10.0 - i}
    for i in range(95)
])
res = C.categorize_transactions(df, model="fake-fast")
print("  rows:", len(df), "| batches:", res.batches, "| summary:", res.summary)
assert res.batches == 3, res.batches               # 40 + 40 + 15
assert len(SEEN) == 3
sizes = [len(re.findall(r"^\d+\. merchant=", p, re.M)) for p, _, _ in SEEN]
print("  batch sizes:", sizes); assert sizes == [40, 40, 15]
body = SEEN[0][0]
assert not BAD.search(body), "identifier leaked into prompt"
assert "ACCT" not in body.upper() and "AUTH" not in body.upper()
print("  prompt carries merchant+amount only, no account numbers")
assert SEEN[0][1] == C.RESPONSE_SCHEMA and SEEN[0][2] == "fake-fast"
print("  schema passed + model = GEMINI_MODEL_FAST override")

print("\n=== rules run first; model only sees the remainder ===")
SEEN.clear()
mixed = pd.DataFrame([
    {"Description": "TRADER JOE'S #417", "Amount": -52.10},
    {"Description": "NETFLIX.COM", "Amount": -15.49},
    {"Description": "ZZQ UNKNOWN VENDOR", "Amount": -8.00},
])
r2 = C.categorize_transactions(mixed, model="fake-fast")
print("  ", r2.summary, "| llm calls:", len(SEEN))
print(r2.frame[["Description", "Category"]].to_string(index=False))
assert r2.rule_hits == 2 and r2.llm_hits == 1 and len(SEEN) == 1
assert len(re.findall(r"^\d+\. merchant=", SEEN[0][0], re.M)) == 1

print("\n=== a failing batch must not fail the import ===")
def flaky(prompt, schema=None, model=None, **kw):
    n = len(re.findall(r"^(\d+)\. merchant=", prompt, re.M))
    if len(SEEN2) == 0:
        SEEN2.append(1); raise llm.RateLimited("429 exhausted")
    SEEN2.append(1)
    idxs = [int(m) for m in re.findall(r"^(\d+)\. merchant=", prompt, re.M)]
    return [{"index": i, "category": "Dining"} for i in idxs]
SEEN2 = []
C.generate = flaky
big = pd.DataFrame([{"Description": f"MYSTERY VENDOR {i}", "Amount": -5.0} for i in range(60)])
bars = []
r3 = C.categorize_transactions(big, model="f", progress=lambda f, m: bars.append((round(f,2), m)))
print("  ", r3.summary)
print("   failures:", r3.failures)
print("   progress:", bars)
assert r3.llm_hits == 20 and r3.unresolved == 40
assert (r3.frame["Category"] == C.UNCATEGORIZED).sum() == 40
assert len(r3.failures) == 1
print("  first batch died, second still applied; 40 left Uncategorized")

print("\n=== invalid categories from the model are rejected ===")
C.generate = lambda p, schema=None, model=None, **k: [
    {"index": 0, "category": "Crypto"}, {"index": 1, "category": "Dining"},
    {"index": 99, "category": "Dining"}, {"index": 2, "category": "Groceries"},
]
r4 = C.categorize_transactions(
    pd.DataFrame([{"Description": f"NOPE {i}", "Amount": -1.0} for i in range(3)]), model="f")
print("  ", r4.frame["Category"].tolist())
assert r4.frame["Category"].tolist() == [C.UNCATEGORIZED, "Dining", "Groceries"]
print("  'Crypto' dropped, out-of-range index 99 ignored")

print("\n=== existing categories are preserved ===")
C.generate = lambda p, schema=None, model=None, **k: []
keep = pd.DataFrame([
    {"Description": "TRADER JOE'S", "Amount": -1.0, "Category": "Dining"},
    {"Description": "TRADER JOE'S", "Amount": -1.0, "Category": ""},
])
r5 = C.categorize_transactions(keep, model="f")
print("  ", r5.frame["Category"].tolist())
assert r5.frame["Category"].tolist() == ["Dining", "Groceries"]

print("\n=== learning a rule from a manual edit ===")
tmp = pathlib.Path(tempfile.mkdtemp()) / "rules.json"
shutil.copy("finance_app/category_rules.json", tmp)
before = len(C.load_rules(tmp))
rule = C.learn_rule("POS DEBIT ZZQ UNKNOWN VENDOR 998812", "Dining", path=tmp)
print("  learned:", rule)
after = C.load_rules(tmp)
assert len(after) == before + 1
assert C.apply_rules(C.scrub_merchant("POS DEBIT ZZQ UNKNOWN VENDOR 445511"), after) == "Dining"
print("  same merchant now matches by rule, no model needed")
assert C.learn_rule("ZZQ UNKNOWN VENDOR", "Dining", path=tmp) is None
print("  duplicate learn is a no-op")
C.learn_rule("ZZQ UNKNOWN VENDOR", "Shopping", path=tmp)
assert C.apply_rules("zzq unknown vendor", C.load_rules(tmp)) == "Shopping"
print("  re-learning replaces the category instead of duplicating")

orig = pd.DataFrame([{"Description": "QQQ CORNER SHOP 1122", "Category": "Shopping"}])
edit = pd.DataFrame([{"Description": "QQQ CORNER SHOP 1122", "Category": "Groceries"}])
got = C.learn_from_edits(orig, edit, path=tmp)
print("  learn_from_edits ->", got); assert len(got) == 1

print("\n=== llm.generate: 429 backoff ===")
class FakeErr(Exception):
    def __init__(self, code): self.code = code; super().__init__(f"{code}")
import google.genai.errors as gerr
llm.errors = type("E", (), {"APIError": FakeErr})
slept, attempts = [], []
llm.time = type("T", (), {"sleep": staticmethod(lambda s: slept.append(round(s)))})
class FakeModels:
    def generate_content(self, **kw):
        attempts.append(1)
        if len(attempts) <= 4: raise FakeErr(429)
        return type("R", (), {"text": "ok", "parsed": {"ok": True}})()
llm._client = lambda: type("C", (), {"models": FakeModels()})()
notes = []
out = llm.generate("hi", schema={"type": "object"}, model="m",
                   on_retry=lambda a, d, e: notes.append(a))
print("  slept:", slept, "| attempts:", len(attempts), "| result:", out)
assert slept == [1, 2, 4, 8], slept
assert len(attempts) == 5 and notes == [1, 2, 3, 4]
print("  exact 1s/2s/4s/8s backoff, succeeded on the 5th attempt")

attempts.clear(); slept.clear()
class AlwaysLimited:
    def generate_content(self, **kw):
        attempts.append(1); raise FakeErr(429)
llm._client = lambda: type("C", (), {"models": AlwaysLimited()})()
try:
    llm.generate("hi", model="m"); print("  FAIL: should have raised")
except llm.RateLimited as e:
    print("  exhausted ->", str(e)[:72] + "...")
assert len(attempts) == 5

attempts.clear()
class Bad400:
    def generate_content(self, **kw):
        attempts.append(1); raise FakeErr(400)
llm._client = lambda: type("C", (), {"models": Bad400()})()
try: llm.generate("hi", model="m")
except llm.LLMError as e: print("  400 not retried ->", str(e)[:50])
assert len(attempts) == 1, "non-retryable status must not retry"

print("\nALL ASSERTIONS PASSED")
