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
