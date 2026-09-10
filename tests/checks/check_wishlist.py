"""Offline checks for wishlist logic and product extraction.

No network: HTML is fed in directly and the HTTP layer is faked, so blocked
retailers and dead sites are exercised as ordinary outcomes.

Run: python tests/test_wishlist.py
"""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import pandas as pd
import requests
from finance_app.logic import wishlist as W
from finance_app import scrape as S
from finance_app.logic.budget import format_currency as fc

TODAY = pd.Timestamp("2026-09-09")

RAW = pd.DataFrame([
    {"Item ID":"w1","Name":"Standing desk","Price":"$599.00","URL":"http://a","Category":"Home",  "Priority":5,"Status":"wanted",     "Added On":"2026-08-10","Notes":"sit/stand","Target Date":"2026-12-01"},
    {"Item ID":"w2","Name":"Headphones",   "Price":349.0,    "URL":"",        "Category":"Tech",  "Priority":4,"Status":"wanted",     "Added On":"2026-09-01","Notes":"noise cancel","Target Date":None},
    {"Item ID":"w3","Name":"Camera",       "Price":1200.0,   "URL":"",        "Category":"Tech",  "Priority":2,"Status":"considering","Added On":"2026-06-15","Notes":"","Target Date":None},
    {"Item ID":"w4","Name":"Old thing",    "Price":80.0,     "URL":"",        "Category":"Home",  "Priority":1,"Status":"bought",     "Added On":"2026-05-01","Notes":"","Target Date":None},
    {"Item ID":"w5","Name":"Skipped thing","Price":45.0,     "URL":"",        "Category":"Other", "Priority":1,"Status":"skipped",    "Added On":"2026-05-02","Notes":"","Target Date":None},
    {"Item ID":"w6","Name":"No date item", "Price":25.0,     "URL":"",        "Category":"Other", "Priority":3,"Status":"wanted",     "Added On":"",          "Notes":"","Target Date":None},
])

print("=== prepare: types, days on list, status normalisation ===")
items = W.prepare(RAW, today=TODAY)
print(items[["Name","Price","Priority","Status","Days On List"]].to_string(index=False))
assert items["Price"].tolist()[0] == 599.0, "currency string parsed"
assert items.loc[0, "Days On List"] == 30      # 10 Aug -> 9 Sep
assert items.loc[1, "Days On List"] == 8
assert pd.isna(items.loc[5, "Days On List"]), "no Added On -> null age, not 0"
print("  '$599.00' -> 599.0; missing Added On -> <NA>, not a fake 0 days")

print("\n=== unknown status falls back to 'wanted' ===")
odd = W.prepare(pd.DataFrame([{"Item ID":"x","Name":"n","Price":1,"Status":"weird"},
                              {"Item ID":"y","Name":"n","Price":1,"Status":""}]), today=TODAY)
print("  ", odd["Status"].tolist())
assert odd["Status"].tolist() == ["wanted","wanted"]

print("\n=== header stats ===")
stats = W.wishlist_stats(items, monthly_surplus=500.0)
print(f"  total (open items)   {fc(stats.total_value)}   = 599+349+1200+25")
print(f"  wanted only          {fc(stats.wanted_value)}   = 599+349+25")
print(f"  wanted count         {stats.wanted_count}")
assert stats.total_value == 2173.0, stats.total_value
assert stats.wanted_value == 973.0, stats.wanted_value
assert stats.wanted_count == 3
print("  bought and skipped excluded from both totals")

print(f"\n  months to clear      {stats.months_to_clear:.2f}  (973 / 500)")
assert round(stats.months_to_clear, 4) == round(973/500, 4)

print("\n=== no surplus -> None, never a misleading number ===")
for surplus in (0.0, -250.0):
    s = W.wishlist_stats(items, monthly_surplus=surplus)
    print(f"  surplus {fc(surplus):>12} -> months_to_clear = {s.months_to_clear}")
    assert s.months_to_clear is None
empty_wanted = W.wishlist_stats(
    W.prepare(RAW[RAW["Status"] != "wanted"], today=TODAY), monthly_surplus=500.0)
assert empty_wanted.months_to_clear == 0.0
print("  nothing wanted, positive surplus -> 0.0 months")

print("\n=== filters ===")
assert len(W.filter_items(items, statuses=["wanted"])) == 3
assert len(W.filter_items(items, categories=["Tech"])) == 2
assert len(W.filter_items(items, priority_range=(4, 5))) == 2
assert len(W.filter_items(items, price_range=(0, 100))) == 3
assert len(W.filter_items(items, search="noise")) == 1
combo = W.filter_items(items, statuses=["wanted"], categories=["Tech"], priority_range=(4,5))
print("  wanted + Tech + priority 4-5 ->", combo["Name"].tolist())
assert combo["Name"].tolist() == ["Headphones"]
assert len(W.filter_items(items)) == len(items), "no filters = everything"

print("\n=== sorting ===")
for label in W.SORT_FIELDS:
    got = W.sort_items(items, by=label, descending=True)
    assert len(got) == len(items), label
print("  by price desc:", W.sort_items(items, "Price", True)["Name"].tolist()[:2])
assert W.sort_items(items, "Price", True)["Name"].iloc[0] == "Camera"
assert W.sort_items(items, "Priority", True)["Name"].iloc[0] == "Standing desk"
nulls_last = W.sort_items(items, "Days on list", False)["Name"].tolist()
print("  null ages sort last:", nulls_last[-1])
assert nulls_last[-1] == "No date item"

print("\n=== empty wishlist ===")
e = W.prepare(pd.DataFrame(), today=TODAY)
assert e.empty and "Days On List" in e.columns
s = W.wishlist_stats(e, 500.0)
assert s.total_value == 0.0 and s.months_to_clear == 0.0
assert W.filter_items(e, statuses=["wanted"]).empty
assert W.categories_in(e) == []
print("  no crash, sensible zeros")

# ---------------------------------------------------------------- scraping
print("\n=== JSON-LD Product wins ===")
JSONLD = '''<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Aeron Chair",
 "offers":{"@type":"Offer","price":"1395.00","priceCurrency":"USD"}}
</script>
<meta property="og:title" content="Wrong OG title">
</head><body></body></html>'''
p = S.extract_product(JSONLD, "http://x")
print(f"  {p.source}: {p.name!r} {fc(p.price)} {p.currency}")
assert p.ok and p.name == "Aeron Chair" and p.price == 1395.0 and p.source == "json-ld"
print("  structured data beats OpenGraph, as specified")

print("\n=== OpenGraph fallback ===")
OG = '''<html><head>
<meta property="og:title" content="Sony WH-1000XM5">
<meta property="product:price:amount" content="399.99">
<meta property="product:price:currency" content="USD">
</head></html>'''
p = S.extract_product(OG, "http://x")
print(f"  {p.source}: {p.name!r} {fc(p.price)} {p.currency}")
assert p.ok and p.price == 399.99 and p.source == "opengraph"

print("\n=== reversed attribute order still parses ===")
REV = '<html><head><meta content="Reversed Co" property="og:title">' \
      '<meta content="12.34" property="product:price:amount"></head></html>'
p = S.extract_product(REV, "http://x")
print(f"  {p.name!r} {fc(p.price)}")
assert p.name == "Reversed Co" and p.price == 12.34

print("\n=== bare meta / title fallback ===")
META = '<html><head><title>  Widget  Pro  </title>' \
       '<meta itemprop="price" content="$79.50"></head></html>'
p = S.extract_product(META, "http://x")
print(f"  {p.source}: {p.name!r} {fc(p.price)}")
assert p.price == 79.5 and "Widget Pro" in p.name

print("\n=== nothing extractable is a normal result, not an error ===")
p = S.extract_product("<html><body>just words</body></html>", "http://x")
print(f"  ok={p.ok} reason={p.reason!r}")
assert p.ok is False and p.price is None and p.name == "" and p.reason

print("\n=== malformed JSON-LD must not crash ===")
BAD = '<html><head><script type="application/ld+json">{not json at all,,,</script>' \
      '<meta property="og:title" content="Still Works"></head></html>'
p = S.extract_product(BAD, "http://x")
print(f"  fell through to {p.source}: {p.name!r}")
assert p.name == "Still Works"

print("\n=== HTTP failure modes all return ok=False, never raise ===")
class FakeResp:
    def __init__(self, code, text=""): self.status_code, self.text = code, text

cases = [
    ("403 blocked",   lambda **k: FakeResp(403), "refused automated access"),
    ("429 throttled", lambda **k: FakeResp(429), "refused automated access"),
    ("500 broken",    lambda **k: FakeResp(500), "HTTP 500"),
    ("timeout",       lambda **k: (_ for _ in ()).throw(requests.Timeout()), "did not respond"),
    ("dns fail",      lambda **k: (_ for _ in ()).throw(requests.ConnectionError()), "Could not reach"),
    ("redirect loop", lambda **k: (_ for _ in ()).throw(requests.TooManyRedirects()), "redirected too many"),
    ("empty body",    lambda **k: FakeResp(200, "   "), "was empty"),
]
real_get = requests.get
try:
    for label, impl, expect in cases:
        requests.get = lambda url, **k: impl(url=url, **k)
        S.requests.get = requests.get
        r = S.fetch_product("http://blocked.example")
        print(f"  {label:14} ok={str(r.ok):5} {r.reason}")
        assert r.ok is False and expect in r.reason, (label, r.reason)
finally:
    requests.get = real_get
    S.requests.get = real_get
print("  every failure is a normal result with a readable reason")

print("\n=== timeout and User-Agent are as specified ===")
seen = {}
def spy(url, **kw):
    seen.update(kw); return FakeResp(200, JSONLD)
S.requests.get = spy
try:
    r = S.fetch_product("example.com/product")
    print("  timeout:", seen["timeout"], "| UA starts:", seen["headers"]["User-Agent"][:38])
    assert seen["timeout"] == 10
    assert "Mozilla/5.0" in seen["headers"]["User-Agent"]
    assert r.ok and r.name == "Aeron Chair"
    print("  bare domain got https:// prefixed and parsed")
finally:
    S.requests.get = real_get

print("\n=== blank / junk URLs ===")
for bad in ("", "   ", None):
    r = S.fetch_product(bad)
    assert r.ok is False and r.reason
print("  handled without a request")

print("\nALL ASSERTIONS PASSED")
