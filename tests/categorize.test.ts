/** Merchant scrubbing and the rules tier, which decide what reaches the model. */

import { describe, expect, it } from "vitest";
import * as C from "../src/lib/categorize";

describe("scrubbing", () => {
  it("reduces a bank description to a bare merchant name", () => {
    expect(C.scrubMerchant("POS DEBIT TRADER JOE'S #417 XXXXXX4412 SAN JOSE CA")).toBe("trader joe's san jose");
  });

  it("keeps a merchant that merely contains x's", () => {
    expect(C.scrubMerchant("EXXON MOBIL 4412")).toContain("exxon");
  });
});

describe("rules", () => {
  const rules = C.sortRules([{ match: "rent", category: "Housing" }, { match: "trader joe", category: "Groceries" }]);

  it("match whole tokens only", () => {
    expect(C.applyRules("parent teacher", rules)).toBeNull();
    expect(C.applyRules("trader joe's san jose", rules)).toBe("Groceries");
  });

  it("a learned rule replaces the shipped one for the same merchant", () => {
    const merged = C.mergeRules([{ match: "trader joe", category: "Dining" }], [{ match: "trader joe", category: "Groceries" }]);
    expect(C.applyRules("trader joe's", merged)).toBe("Dining");
  });

  it("learns only real corrections", () => {
    const learned = C.learnFromEdits(
      [
        { description: "POS DEBIT CORNER CAFE #12", category: "Dining" },
        { description: "ALDI 3003", category: "Groceries" },
        { description: "MYSTERY 99", category: C.UNCATEGORIZED },
      ],
      ["Other", "Groceries", "Other"],
      [],
    );
    expect(learned).toEqual([{ match: "corner cafe", category: "Dining" }]);
  });
});
