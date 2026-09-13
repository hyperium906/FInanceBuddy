/**
 * Reading a product's name and price out of a page. Every path returns a
 * result rather than throwing, so a blocked page explains itself.
 */

import { describe, expect, it } from "vitest";
import { extractProduct, hasAnything } from "../src/lib/products";

const JSON_LD = `
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Adidas Sambas",
 "offers":{"@type":"Offer","price":"100.00","priceCurrency":"USD"}}
</script></head><body></body></html>`;

const OPENGRAPH = `
<html><head>
<meta property="og:title" content="Whoop 4.0">
<meta property="product:price:amount" content="239.00">
<meta property="product:price:currency" content="USD">
</head><body></body></html>`;

const VISIBLE = `
<html><head><title>A Light in the Attic | Books to Scrape</title></head>
<body><div class="product_main"><h1>A Light in the Attic</h1>
<p class="price_color">£51.77</p></div></body></html>`;

describe("declared data", () => {
  it("reads JSON-LD, then OpenGraph", () => {
    const sambas = extractProduct(JSON_LD, "https://example.com/sambas");
    expect([sambas.ok, sambas.name, sambas.price]).toEqual([true, "Adidas Sambas", 100]);
    const whoop = extractProduct(OPENGRAPH, "https://example.com/whoop");
    expect([whoop.name, whoop.price]).toEqual(["Whoop 4.0", 239]);
  });

  it("a name with no price is still useful", () => {
    const info = extractProduct('<html><head><meta property="og:title" content="Roli Piano"></head></html>', "https://x.com");
    expect([hasAnything(info), info.name]).toEqual([true, "Roli Piano"]);
  });
});

describe("failure is a result", () => {
  it("a blocked page and garbage do not throw", () => {
    const blocked = extractProduct("<html><head><title>Access Denied</title></head><body>Blocked</body></html>", "https://x.com");
    expect([blocked.ok, hasAnything(blocked)]).toEqual([false, false]);
    expect(blocked.reason).toBeTruthy();
    for (const html of ["", "<html>", "not html at all", "<script type='application/ld+json'>{"]) {
      expect(() => extractProduct(html, "https://example.com")).not.toThrow();
    }
  });

  it.each(["Access Denied", "Robot Check", "Just a moment...", "Attention Required! | Cloudflare", "403 Forbidden", "Verify you are human"])(
    "a bot wall titled %s is not read as a product name", (title) => {
      const info = extractProduct(`<html><head><title>${title}</title></head></html>`, "https://x.com");
      expect([info.ok, hasAnything(info)]).toEqual([false, false]);
      expect(info.reason).toContain("refused");
    },
  );

  it("a bare title is offered but not called a success; with a price it is", () => {
    const bare = extractProduct("<html><head><title>Adidas Sambas OG Shoes</title></head></html>", "https://x.com");
    expect([hasAnything(bare), bare.ok]).toEqual([true, false]);
    expect(bare.reason.toLowerCase()).toContain("title");
    const priced = extractProduct('<html><head><title>Sambas</title><meta itemprop="price" content="100.00"></head></html>', "https://x.com");
    expect([priced.ok, priced.price]).toEqual([true, 100]);
  });

  it("a trailing site name is stripped from a title", () => {
    expect(extractProduct("<html><head><title>Adidas Sambas | adidas US</title></head></html>", "https://x.com").name).toBe("Adidas Sambas");
  });
});

describe("reading the page body", () => {
  it("finds a price in the markup, preferring the heading to the title", () => {
    const info = extractProduct(VISIBLE, "https://books.example/a");
    expect([info.ok, info.name, info.price, info.source]).toEqual([true, "A Light in the Attic", 51.77, "html"]);
  });

  it.each([
    ['<span class="a-price-whole">499</span>', 499],
    ['<div class="product-price">$1,299.00</div>', 1299],
    ['<span class="sale-price">$49.99</span>', 49.99],
    ['<p id="current-price">€89.50</p>', 89.5],
  ])("common price markup %s", (markup, expected) => {
    expect(extractProduct(`<html><body><h1>Thing</h1>${markup}</body></html>`, "https://x.com").price).toBeCloseTo(expected);
  });

  it("a name with no price anywhere is offered, not claimed", () => {
    const info = extractProduct("<html><body><h1>Roli Piano</h1></body></html>", "https://x.com");
    expect([hasAnything(info), info.ok]).toEqual([true, false]);
    expect(info.reason).toContain("no price");
  });

  it("structured data still wins over the body", () => {
    const info = extractProduct(JSON_LD.replace("</body>", '<h1>Wrong Name</h1><p class="price">$1.00</p></body>'), "https://x.com");
    expect([info.name, info.price]).toEqual(["Adidas Sambas", 100]);
  });
});
