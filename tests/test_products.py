"""Reading a product's name and price out of a page.

Auto-fill failing is ordinary, not exceptional: large retailers block
automated readers as a matter of course. Every one of these paths has to
return a result object rather than raise, so a blocked page leaves the form
empty and explains itself instead of taking the page down.
"""

from __future__ import annotations

import pytest

from financebuddy.data.products import ProductInfo, extract_product

JSON_LD = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Adidas Sambas",
 "offers":{"@type":"Offer","price":"100.00","priceCurrency":"USD"}}
</script></head><body></body></html>
"""

OPENGRAPH = """
<html><head>
<meta property="og:title" content="Whoop 4.0">
<meta property="product:price:amount" content="239.00">
<meta property="product:price:currency" content="USD">
</head><body></body></html>
"""

NOTHING = "<html><head><title>Access Denied</title></head><body>Blocked</body></html>"


def test_json_ld_is_read():
    info = extract_product(JSON_LD, "https://example.com/sambas")
    assert info.ok
    assert info.name == "Adidas Sambas"
    assert info.price == pytest.approx(100.0)


def test_opengraph_is_read_when_there_is_no_json_ld():
    info = extract_product(OPENGRAPH, "https://example.com/whoop")
    assert info.name == "Whoop 4.0"
    assert info.price == pytest.approx(239.0)


def test_a_blocked_page_is_a_result_not_an_exception():
    info = extract_product(NOTHING, "https://example.com/x")
    assert isinstance(info, ProductInfo)
    assert not info.ok
    assert not info.has_anything
    assert info.reason


def test_garbage_does_not_raise():
    for html in ("", "<html>", "not html at all", "<script type='application/ld+json'>{"):
        assert isinstance(extract_product(html, "https://example.com"), ProductInfo)


def test_a_name_with_no_price_is_still_useful():
    info = extract_product(
        '<html><head><meta property="og:title" content="Roli Piano"></head></html>',
        "https://example.com/roli",
    )
    assert info.has_anything
    assert info.name == "Roli Piano"


@pytest.mark.parametrize("title", [
    "Access Denied", "Robot Check", "Just a moment...", "Attention Required! | Cloudflare",
    "403 Forbidden", "Verify you are human",
])
def test_a_bot_wall_is_not_read_as_a_product_name(title):
    """Filling the form with "Robot Check" is worse than filling nothing."""
    info = extract_product(f"<html><head><title>{title}</title></head></html>", "https://x.com")
    assert not info.ok
    assert not info.has_anything
    assert "refused" in info.reason


def test_a_bare_title_is_offered_but_not_called_a_success():
    """Every page has a title; it is as likely the site's name as the product's."""
    info = extract_product(
        "<html><head><title>Adidas Sambas OG Shoes</title></head></html>", "https://x.com")
    assert info.has_anything
    assert not info.ok
    assert "title" in info.reason.lower()


def test_a_title_with_a_price_is_a_success():
    info = extract_product(
        '<html><head><title>Sambas</title>'
        '<meta itemprop="price" content="100.00"></head></html>', "https://x.com")
    assert info.ok
    assert info.price == pytest.approx(100.0)


# --------------------------------------------------------------------------
# Reading the page body
# --------------------------------------------------------------------------

VISIBLE = """
<html><head><title>A Light in the Attic | Books to Scrape</title></head>
<body><div class="product_main"><h1>A Light in the Attic</h1>
<p class="price_color">£51.77</p></div></body></html>
"""


def test_a_price_in_the_markup_is_found():
    """Plenty of shops declare nothing; the price is simply in the body."""
    info = extract_product(VISIBLE, "https://books.example/a")
    assert info.ok
    assert info.name == "A Light in the Attic"
    assert info.price == pytest.approx(51.77)
    assert info.source == "html"


def test_the_heading_beats_the_title():
    """A <title> carries the shop's name too; an <h1> is the product."""
    assert extract_product(VISIBLE, "https://x.com").name == "A Light in the Attic"


@pytest.mark.parametrize("markup, expected", [
    ('<span class="a-price-whole">499</span>', 499.0),
    ('<div class="product-price">$1,299.00</div>', 1299.0),
    ('<span class="sale-price">$49.99</span>', 49.99),
    ('<p id="current-price">€89.50</p>', 89.5),
])
def test_common_price_markup(markup, expected):
    info = extract_product(f"<html><body><h1>Thing</h1>{markup}</body></html>", "https://x.com")
    assert info.price == pytest.approx(expected)


def test_a_name_with_no_price_anywhere_is_offered_not_claimed():
    info = extract_product("<html><body><h1>Roli Piano</h1></body></html>", "https://x.com")
    assert info.has_anything
    assert not info.ok
    assert "no price" in info.reason


def test_a_trailing_site_name_is_stripped_from_a_title():
    info = extract_product(
        "<html><head><title>Adidas Sambas | adidas US</title></head></html>", "https://x.com")
    assert info.name == "Adidas Sambas"


def test_structured_data_still_wins_over_the_body():
    """Declared data is more reliable than guessing at markup."""
    both = JSON_LD.replace("</body>", '<h1>Wrong Name</h1><p class="price">$1.00</p></body>')
    info = extract_product(both, "https://x.com")
    assert info.name == "Adidas Sambas"
    assert info.price == pytest.approx(100.0)
