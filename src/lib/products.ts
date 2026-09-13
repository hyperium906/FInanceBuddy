/**
 * Best-effort product details from a URL.
 *
 * Large retailers block scrapers routinely, so **failure is a normal outcome,
 * not an error**. Nothing here throws: a `ProductInfo` comes back with `ok`
 * false and a `reason`, and the reader types the details in by hand.
 *
 * Extraction is tried cheapest-signal-first: JSON-LD `Product`, OpenGraph
 * tags, the visible `<h1>` and price markup, then bare meta tags and the title.
 */

export interface ProductInfo {
  url: string;
  name: string;
  price: number | null;
  currency: string;
  /** Which extractor won: "json-ld", "opengraph", "html", "meta". */
  source: string;
  ok: boolean;
  /** Why it failed, when it did. */
  reason: string;
}

export const hasAnything = (info: ProductInfo) => Boolean(info.name) || info.price !== null;

const USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";
const TIMEOUT_MS = 10_000;
const MAX_CHARS = 3_000_000;

const result = (url: string, fields: Partial<ProductInfo> = {}): ProductInfo => ({
  url, name: "", price: null, currency: "", source: "", ok: false, reason: "", ...fields,
});

/** Read a product's name and price from `url`. Never throws. */
export async function fetchProduct(url: string): Promise<ProductInfo> {
  let clean = (url || "").trim();
  if (!clean) return result(url, { reason: "No URL given." });
  if (!/^https?:\/\//i.test(clean)) clean = `https://${clean}`;

  let response: Response;
  try {
    response = await fetch(clean, {
      headers: {
        "User-Agent": USER_AGENT,
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
      },
      redirect: "follow",
      signal: AbortSignal.timeout(TIMEOUT_MS),
      cache: "no-store",
    });
  } catch (error) {
    const name = (error as Error)?.name;
    if (name === "TimeoutError" || name === "AbortError") {
      return result(clean, { reason: `The site did not respond within ${TIMEOUT_MS / 1000}s.` });
    }
    return result(clean, { reason: `Could not reach the site: ${name || "network error"}.` });
  }

  if ([401, 403, 429].includes(response.status)) {
    return result(clean, { reason: `The site refused automated access (HTTP ${response.status}).` });
  }
  if (response.status >= 400) return result(clean, { reason: `The site returned HTTP ${response.status}.` });

  let html: string;
  try {
    html = (await response.text()).slice(0, MAX_CHARS);
  } catch {
    return result(clean, { reason: "The page could not be read." });
  }
  if (!html.trim()) return result(clean, { reason: "The page was empty." });
  return extractProduct(html, clean);
}

/** Pull name and price out of `html`. Pure — no network, never throws. */
export function extractProduct(html: string, url = ""): ProductInfo {
  for (const extractor of [fromStructuredData, fromOpenGraph, fromVisibleHtml, fromMetaTags]) {
    let found: ProductInfo | null = null;
    try {
      found = extractor(html, url);
    } catch {
      continue;
    }
    if (found && hasAnything(found)) return found;
    // A bot wall names itself; "the site refused" calls for a different next
    // move than "nothing found".
    if (found?.reason) return found;
  }
  return result(url, { reason: "No product details found on the page (no JSON-LD, OpenGraph, or price tags)." });
}

// --------------------------------------------------------------------------
// Extractors
// --------------------------------------------------------------------------

function fromStructuredData(html: string, url: string): ProductInfo | null {
  const blocks = html.matchAll(/<script[^>]*type\s*=\s*["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi);
  for (const [, body] of blocks) {
    let data: unknown;
    try {
      data = JSON.parse(body.trim());
    } catch {
      continue;
    }
    for (const node of walk(data)) {
      if (!isProduct(node)) continue;
      const name = firstString(node.name);
      const [price, currency] = priceFromOffers(node);
      if (name || price !== null) return result(url, { name, price, currency, source: "json-ld", ok: true });
    }
  }
  return null;
}

function fromOpenGraph(html: string, url: string): ProductInfo | null {
  const tags = metaMap(html);
  const name = (tags["og:title"] || tags["twitter:title"] || "").trim();
  const price = toPrice(tags["product:price:amount"] || tags["og:price:amount"] || tags["twitter:data1"]);
  const currency = (tags["product:price:currency"] || tags["og:price:currency"] || "").trim();
  if (name || price !== null) return result(url, { name, price, currency, source: "opengraph", ok: true });
  return null;
}

/** Most specific first, so a "sale price" beats a generic "price" wrapper. */
const PRICE_MARKERS = [
  "price_color", "product-price", "productprice", "sale-price", "saleprice",
  "current-price", "currentprice", "a-price-whole", "price-now", "our-price",
  "x-price", "price",
];

/** Plenty of shops declare nothing; the price is simply in the markup. */
function fromVisibleHtml(html: string, url: string): ProductInfo | null {
  const heading = /<h1[^>]*>([\s\S]*?)<\/h1>/i.exec(html);
  const name = heading ? stripTags(heading[1]).slice(0, 120) : "";

  let price: number | null = null;
  outer: for (const marker of PRICE_MARKERS) {
    const pattern = new RegExp(
      `<[^>]+(?:class|id|data-testid)\\s*=\\s*["'][^"']*${escapeRegExp(marker)}[^"']*["'][^>]*>([\\s\\S]{0,200}?)</`,
      "gi",
    );
    for (const found of html.matchAll(pattern)) {
      price = toPrice(stripTags(found[1]));
      if (price) break outer;
    }
  }

  if (name || price !== null) {
    return result(url, {
      name, price, source: "html", ok: price !== null,
      reason: price !== null ? "" : "Found a name but no price — check it.",
    });
  }
  return null;
}

/** Titles a bot-block page wears. Filling the form with "Robot Check" is worse than nothing. */
const BLOCKED_TITLES = [
  "access denied", "403 forbidden", "forbidden", "robot check", "are you a robot",
  "just a moment", "attention required", "security check", "captcha",
  "request blocked", "page not found", "404", "error", "bot detection",
  "unusual traffic", "verify you are human", "pardon our interruption",
];

function fromMetaTags(html: string, url: string): ProductInfo | null {
  const tags = metaMap(html);
  const price = toPrice(tags["price"] || tags["itemprop:price"]);
  let name = (tags["itemprop:name"] || "").trim();

  let titled = false;
  if (!name) {
    const match = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html);
    if (match) {
      const candidate = match[1].replace(/\s+/g, " ").trim().slice(0, 120);
      if (BLOCKED_TITLES.some((marker) => candidate.toLowerCase().includes(marker))) {
        return result(url, { reason: "The site refused automated access." });
      }
      name = cleanTitle(candidate);
      titled = true;
    }
  }

  if (name || price !== null) {
    // A title with no price is the weakest evidence there is: worth offering,
    // not worth calling a success.
    const weak = titled && price === null;
    return result(url, {
      name, price, source: "meta", ok: !weak,
      reason: weak ? "Only the page title could be read — check it." : "",
    });
  }
  return null;
}

// --------------------------------------------------------------------------
// Parsing helpers
// --------------------------------------------------------------------------

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** A trailing site name, stripped once: "Adidas Sambas | adidas US" -> "Adidas Sambas". */
function cleanTitle(title: string): string {
  const trimmed = title.replace(/\s*[|–—:-]\s*[^|–—:-]{1,40}$/, "").trim();
  return trimmed.length >= 3 ? trimmed : title.trim();
}

function stripTags(fragment: string): string {
  return fragment
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&").replace(/&nbsp;/g, " ").replace(/&#39;/g, "'").replace(/&quot;/g, '"')
    .replace(/\s+/g, " ")
    .trim();
}

/** Every `<meta>` name/property/itemprop pair, in either attribute order. */
function metaMap(html: string): Record<string, string> {
  const tags: Record<string, string> = {};
  const forward = /<meta\b[^>]*?(?:property|name|itemprop)\s*=\s*["']([^"']+)["'][^>]*?content\s*=\s*["']([^"']*)["']/gi;
  const reversed = /<meta\b[^>]*?content\s*=\s*["']([^"']*)["'][^>]*?(?:property|name|itemprop)\s*=\s*["']([^"']+)["']/gi;
  for (const [, key, value] of html.matchAll(forward)) tags[key.trim().toLowerCase()] ??= value.trim();
  for (const [, value, key] of html.matchAll(reversed)) tags[key.trim().toLowerCase()] ??= value.trim();
  return tags;
}

type Node = Record<string, unknown>;

function* walk(node: unknown, depth = 0): Generator<Node> {
  if (depth > 8) return;
  if (Array.isArray(node)) {
    for (const value of node) yield* walk(value, depth + 1);
  } else if (node && typeof node === "object") {
    yield node as Node;
    for (const value of Object.values(node)) yield* walk(value, depth + 1);
  }
}

function isProduct(node: Node): boolean {
  const raw = node["@type"] ?? node["type"] ?? "";
  return (Array.isArray(raw) ? raw : [raw]).some((v) => String(v).toLowerCase().includes("product"));
}

function firstString(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = firstString(item);
      if (found) return found;
    }
  }
  if (value && typeof value === "object") {
    for (const key of ["@value", "value", "name"]) {
      if (key in value) return firstString((value as Node)[key]);
    }
  }
  return "";
}

function priceFromOffers(node: Node): [number | null, string] {
  for (const offer of walk(node.offers)) {
    const price = toPrice(offer.price ?? offer.lowPrice ?? offer.highPrice);
    if (price !== null) return [price, firstString(offer.priceCurrency)];
  }
  return [toPrice(node.price), firstString(node.priceCurrency)];
}

/**
 * A price out of a string or number: `$1,299.00`, `1.299,00` (European),
 * `USD 49.99`. Null for anything unusable or non-positive.
 */
export function toPrice(raw: unknown): number | null {
  if (raw === null || raw === undefined || typeof raw === "boolean") return null;
  if (typeof raw === "number") return raw > 0 ? raw : null;

  let text = (typeof raw === "string" ? raw : firstString(raw)).replace(/[^\d.,-]/g, "").trim();
  if (!text) return null;

  if (text.includes(",") && text.includes(".")) {
    text = text.lastIndexOf(",") > text.lastIndexOf(".")
      ? text.replace(/\./g, "").replace(",", ".")
      : text.replace(/,/g, "");
  } else if ((text.match(/,/g) ?? []).length === 1 && text.split(",").pop()!.length === 2) {
    text = text.replace(",", ".");
  } else {
    text = text.replace(/,/g, "");
  }

  const value = Number(text);
  return Number.isFinite(value) && value > 0 ? value : null;
}
