"""Best-effort product details from a URL.

Large retailers block scrapers routinely, so **failure is a normal outcome
here, not an error**. :func:`fetch_product` never raises: it returns a
:class:`ProductInfo` whose ``ok`` is False and whose ``reason`` explains what
happened, leaving the caller to let the user type the details in by hand.

Extraction is tried cheapest-signal-first:

1. JSON-LD ``Product`` schema (via extruct) — the most reliable when present
2. Microdata / RDFa ``Product`` (also via extruct)
3. OpenGraph tags (``og:title``, ``product:price:amount``, …)
4. Common bare meta price tags (``itemprop="price"``, ``twitter:data1``, …)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

import requests

log = logging.getLogger(__name__)

#: A realistic browser UA. Many sites reject obvious bot agents outright.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

#: Seconds before giving up on a slow site.
TIMEOUT = 10

#: Cap on how much HTML to parse, so a huge page cannot stall the app.
MAX_BYTES = 3_000_000


@dataclass(frozen=True, slots=True)
class ProductInfo:
    """What could be read from a product page. Never an exception."""

    url: str
    name: str = ""
    price: float | None = None
    currency: str = ""
    source: str = ""      # which extractor won: "json-ld", "opengraph", …
    ok: bool = False
    reason: str = ""      # why it failed, when it did

    @property
    def has_anything(self) -> bool:
        """True when at least one useful field came back."""
        return bool(self.name) or self.price is not None


def fetch_product(url: str, timeout: int = TIMEOUT) -> ProductInfo:
    """Read a product's name and price from ``url``. Never raises.

    Returns a :class:`ProductInfo`; check ``ok`` and ``reason``. A blocked or
    unparseable page is an ordinary result, not a failure of the app.
    """
    clean = (url or "").strip()
    if not clean:
        return ProductInfo(url=url, reason="No URL given.")
    if not clean.lower().startswith(("http://", "https://")):
        clean = f"https://{clean}"

    try:
        response = requests.get(
            clean, headers=HEADERS, timeout=timeout, allow_redirects=True
        )
    except requests.Timeout:
        return ProductInfo(url=clean, reason=f"The site did not respond within {timeout}s.")
    except requests.TooManyRedirects:
        return ProductInfo(url=clean, reason="The site redirected too many times.")
    except requests.RequestException as exc:
        return ProductInfo(url=clean, reason=f"Could not reach the site: {type(exc).__name__}.")

    if response.status_code in (401, 403, 429):
        return ProductInfo(
            url=clean,
            reason=f"The site refused automated access (HTTP {response.status_code}).",
        )
    if response.status_code >= 400:
        return ProductInfo(url=clean, reason=f"The site returned HTTP {response.status_code}.")

    html = response.text[:MAX_BYTES]
    if not html.strip():
        return ProductInfo(url=clean, reason="The page was empty.")

    return extract_product(html, clean)


def extract_product(html: str, url: str = "") -> ProductInfo:
    """Pull name and price out of ``html``. Pure — no network, never raises."""
    for extractor in (_from_structured_data, _from_opengraph, _from_meta_tags):
        try:
            found = extractor(html, url)
        except Exception as exc:  # noqa: BLE001 - a bad page must not propagate
            log.debug("%s failed on %s: %s", extractor.__name__, url, exc)
            continue
        if found and found.has_anything:
            return found
        # An extractor may also come back empty *with* a reason — a bot wall
        # names itself, and "the site refused" is a better thing to tell
        # somebody than "nothing found", because the two call for different
        # next moves: type it in by hand, versus check the link.
        if found is not None and found.reason:
            return found

    return ProductInfo(
        url=url,
        reason="No product details found on the page (no JSON-LD, OpenGraph, or price tags).",
    )


# --------------------------------------------------------------------------
# Extractors
# --------------------------------------------------------------------------


def _from_structured_data(html: str, url: str) -> ProductInfo | None:
    """JSON-LD, microdata, and RDFa ``Product`` entries, via extruct."""
    import extruct

    data = extruct.extract(
        html, base_url=url or None, syntaxes=["json-ld", "microdata", "rdfa"], uniform=True
    )

    for syntax in ("json-ld", "microdata", "rdfa"):
        for node in _walk(data.get(syntax) or []):
            if not isinstance(node, dict):
                continue
            if not _is_product(node):
                continue
            name = _first_string(node.get("name"))
            price, currency = _price_from_offers(node)
            if name or price is not None:
                return ProductInfo(
                    url=url, name=name, price=price, currency=currency,
                    source=syntax, ok=True,
                )
    return None


def _from_opengraph(html: str, url: str) -> ProductInfo | None:
    """OpenGraph product tags."""
    tags = _meta_map(html)
    name = tags.get("og:title") or tags.get("twitter:title") or ""
    price = _to_price(
        tags.get("product:price:amount")
        or tags.get("og:price:amount")
        or tags.get("twitter:data1")
    )
    currency = (
        tags.get("product:price:currency") or tags.get("og:price:currency") or ""
    ).strip()
    if name or price is not None:
        return ProductInfo(
            url=url, name=name.strip(), price=price, currency=currency,
            source="opengraph", ok=True,
        )
    return None


#: Titles a bot-block page wears. A blocked page still has a ``<title>``, and
#: reading it as the product name is worse than reading nothing: the form
#: fills with "Robot Check" and the reader has to notice and undo it, rather
#: than being told the site refused and typing the name themselves.
_BLOCKED_TITLES = (
    "access denied", "403 forbidden", "forbidden", "robot check", "are you a robot",
    "just a moment", "attention required", "security check", "captcha",
    "request blocked", "page not found", "404", "error", "bot detection",
    "unusual traffic", "verify you are human", "pardon our interruption",
)


def _looks_blocked(title: str) -> bool:
    """Whether a document title is a bot wall rather than a product."""
    lowered = title.strip().lower()
    return any(marker in lowered for marker in _BLOCKED_TITLES)


def _from_meta_tags(html: str, url: str) -> ProductInfo | None:
    """Bare ``itemprop`` price/name tags and, as a last resort, the title."""
    tags = _meta_map(html)
    price = _to_price(tags.get("price") or tags.get("itemprop:price"))
    name = (tags.get("itemprop:name") or "").strip()

    titled = False
    if not name:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if match:
            candidate = re.sub(r"\s+", " ", match.group(1)).strip()[:120]
            if _looks_blocked(candidate):
                return ProductInfo(
                    url=url,
                    reason="The site refused automated access.",
                )
            name, titled = candidate, True

    if name or price is not None:
        return ProductInfo(
            url=url, name=name, price=price, source="meta",
            # A document title with no price is the weakest evidence there is —
            # every page has one, and it is as likely to be the site's name as
            # the product's. Worth offering, not worth calling a success.
            ok=not (titled and price is None),
            reason=("" if not (titled and price is None)
                    else "Only the page title could be read — check it."),
        )
    return None


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

_META = re.compile(
    r"<meta\b[^>]*?(?:property|name|itemprop)\s*=\s*[\"']([^\"']+)[\"'][^>]*?"
    r"content\s*=\s*[\"']([^\"']*)[\"']",
    re.I,
)
_META_REVERSED = re.compile(
    r"<meta\b[^>]*?content\s*=\s*[\"']([^\"']*)[\"'][^>]*?"
    r"(?:property|name|itemprop)\s*=\s*[\"']([^\"']+)[\"']",
    re.I,
)


def _meta_map(html: str) -> dict[str, str]:
    """All ``<meta>`` name/property/itemprop pairs, lower-cased keys.

    Handles both attribute orders, since ``content`` may come before or after
    the name attribute.
    """
    tags: dict[str, str] = {}
    for key, value in _META.findall(html):
        tags.setdefault(key.strip().lower(), value.strip())
    for value, key in _META_REVERSED.findall(html):
        tags.setdefault(key.strip().lower(), value.strip())
    return tags


def _walk(node: Any, depth: int = 0) -> Iterable[Any]:
    """Yield every dict nested anywhere inside ``node``."""
    if depth > 8:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value, depth + 1)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _walk(value, depth + 1)


def _is_product(node: dict) -> bool:
    """True when a structured-data node describes a Product."""
    raw = node.get("@type") or node.get("type") or ""
    types = raw if isinstance(raw, (list, tuple)) else [raw]
    return any("product" in str(value).lower() for value in types)


def _first_string(value: Any) -> str:
    """First usable string out of a value that may be nested or a list."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _first_string(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("@value", "value", "name"):
            if key in value:
                return _first_string(value[key])
    return ""


def _price_from_offers(node: dict) -> tuple[float | None, str]:
    """Price and currency from a Product's ``offers``, or the node itself."""
    for offer in _walk(node.get("offers")):
        if not isinstance(offer, dict):
            continue
        price = _to_price(
            offer.get("price")
            or offer.get("lowPrice")
            or offer.get("highPrice")
        )
        if price is not None:
            currency = _first_string(offer.get("priceCurrency"))
            return price, currency
    return _to_price(node.get("price")), _first_string(node.get("priceCurrency"))


_PRICE_NOISE = re.compile(r"[^\d.,\-]")


def _to_price(raw: Any) -> float | None:
    """Parse a price out of a string or number, tolerating currency noise.

    Handles ``"$1,299.00"``, ``"1.299,00"`` (European), ``"USD 49.99"``, and
    plain numbers. Returns None for anything unusable or non-positive.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw) if raw > 0 else None

    text = _first_string(raw) if not isinstance(raw, str) else raw
    text = _PRICE_NOISE.sub("", str(text)).strip()
    if not text:
        return None

    # European "1.299,00" -> comma is the decimal separator.
    if "," in text and "." in text:
        text = (
            text.replace(".", "").replace(",", ".")
            if text.rindex(",") > text.rindex(".")
            else text.replace(",", "")
        )
    elif text.count(",") == 1 and len(text.split(",")[-1]) == 2:
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")

    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None
