/**
 * Reading a bank export, and surviving being handed it twice. The failure
 * guarded against is not an exception — it is a clean read of the wrong thing.
 */

import { describe, expect, it } from "vitest";
import * as St from "../src/lib/statements";
import { d } from "./helpers";

const CHASE =
  "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n" +
  'DEBIT,09/11/2026,"ALDI INC 3003 SOMEWHERE GA  09/11",-110.34,DEBIT_CARD,2080.59,,\n' +
  'DEBIT,09/10/2026,"COFFEE SHOP MAIN ST GA      09/10",-4.55,DEBIT_CARD,2190.93,,\n' +
  'CREDIT,09/04/2026,"EMPLOYER PAYROLL DIRECT DEP",2065.20,ACH_CREDIT,4256.13,,\n';

const read = (text = CHASE) => St.readBankCsv(text);
const normalised = (text = CHASE) => {
  const raw = read(text);
  return St.normalise(raw, St.guessColumns(raw), "chase_checking");
};
const cell = (raw: St.RawCsv, column: string, row = 0) => raw.rows[row][raw.columns.indexOf(column)];

describe("reading", () => {
  it("a trailing comma does not shift every column, and the empty column is dropped", () => {
    const raw = read();
    expect(cell(raw, "Posting Date")).toBe("09/11/2026");
    expect(cell(raw, "Amount")).toBe("-110.34");
    expect(raw.columns.some((c) => c.startsWith("Unnamed:"))).toBe(false);
  });

  it("a populated unnamed column is kept", () => {
    expect(read("Date,Description,Amount,\n2026-09-11,SHOP,-1.00,note\n").rows[0][3]).toBe("note");
  });

  it("guesses the columns, preferring posting date", () => {
    expect(St.guessColumns(read())).toEqual({ Date: "Posting Date", Description: "Description", Amount: "Amount" });
    expect(St.guessColumns(read("Transaction Date,Posting Date,Description,Amount\n09/10/2026,09/11/2026,SHOP,-1.00\n")).Date).toBe("Posting Date");
  });
});

describe("normalising", () => {
  it("parses dates, amounts and the account", () => {
    const [first] = normalised();
    expect([first.date, first.amount, first.accountId]).toEqual([d("2026-09-11"), -110.34, "chase_checking"]);
  });

  it("currency formatting survives", () => {
    const raw = read('Date,Description,Amount\n2026-09-11,SHOP,"$1,234.56"\n');
    expect(St.normalise(raw, St.guessColumns(raw))[0].amount).toBeCloseTo(1234.56);
  });

  it("an unreadable row is kept for correction, not dropped", () => {
    const raw = read("Date,Description,Amount\nnot-a-date,SHOP,nonsense\n");
    const out = St.normalise(raw, St.guessColumns(raw));
    expect(out).toHaveLength(1);
    expect([out[0].date, out[0].amount]).toEqual([null, 0]);
  });
});

describe("deduplicating", () => {
  it("re-uploading the same file adds nothing", () => {
    const again = St.deduplicate(normalised(), normalised());
    expect(again.fresh).toEqual([]);
    expect(again.alreadyHeld).toBe(3);
  });

  it("an overlapping export keeps only what is new", () => {
    const result = St.deduplicate(normalised(), normalised().slice(0, 2));
    expect(result.fresh).toHaveLength(1);
    expect(result.alreadyHeld).toBe(2);
    expect(result.fresh[0].description.startsWith("EMPLOYER")).toBe(true);
  });

  it("a row edited after import is still recognised", () => {
    const held = normalised().map((row) => ({ ...row, category: "Groceries", notes: "corrected by hand" }));
    expect(St.deduplicate(normalised(), held).fresh).toEqual([]);
  });

  it("rows repeated inside one file are counted separately", () => {
    const result = St.deduplicate([...normalised(), normalised()[0]], []);
    expect([result.repeatedInFile, result.alreadyHeld, result.fresh.length]).toEqual([1, 0, 3]);
  });

  it("two genuinely different rows on one day both survive", () => {
    const raw = read("Date,Description,Amount\n2026-09-11,CHICK-FIL-A,-5.34\n2026-09-11,CHICK-FIL-A,-13.34\n");
    expect(St.deduplicate(St.normalise(raw, St.guessColumns(raw)), []).fresh).toHaveLength(2);
  });

  it("an empty sheet takes everything", () => {
    const result = St.deduplicate(normalised(), []);
    expect([result.fresh.length, St.dropped(result)]).toEqual([3, 0]);
  });
});

describe("rows logged by hand", () => {
  const logged = (date: string, amount: number, notes = St.LOGGED_BY_HAND) =>
    [{ date: d(date), description: "Tithe", amount, category: "Giving", accountId: "chase_checking", notes }];
  const bank = (...lines: string[]) => {
    const raw = read(`Date,Description,Amount\n${lines.join("\n")}\n`);
    return St.normalise(raw, St.guessColumns(raw));
  };

  it("the bank line for a logged gift is skipped", () => {
    const result = St.deduplicate(bank("2026-09-15,HOPE CHURCH 478-9537898 GA,-206.52"), logged("2026-09-13", -206.52));
    expect([result.fresh.length, result.loggedByHand, result.alreadyHeld]).toEqual([0, 1, 0]);
  });

  it("a logged row claims only one bank line", () => {
    const result = St.deduplicate(bank("2026-09-14,HOPE CHURCH,-206.52", "2026-09-15,HOPE CHURCH,-206.52"), logged("2026-09-13", -206.52));
    expect([result.fresh.length, result.loggedByHand]).toEqual([1, 1]);
  });

  it("a line outside the window is not claimed, nor by an imported row", () => {
    expect(St.deduplicate(bank("2026-09-25,HOPE CHURCH,-206.52"), logged("2026-09-13", -206.52)).fresh).toHaveLength(1);
    expect(St.deduplicate(bank("2026-09-15,HOPE CHURCH,-206.52"), logged("2026-09-13", -206.52, "")).fresh).toHaveLength(1);
  });

  it("span reports the range", () => {
    expect(St.span(normalised())).toEqual([d("2026-09-04"), d("2026-09-11")]);
  });
});
