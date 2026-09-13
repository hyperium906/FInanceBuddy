/**
 * The Budgets page — each category's monthly figure in `_Budgets`, which the
 * Payday page turns into a per-check allowance.
 */

import Link from "next/link";
import { withSheet } from "@/components/guard";
import { PageTitle } from "@/components/ui";
import { CATEGORIES } from "@/lib/categorize";
import { addMonths, day, format, parts } from "@/lib/dates";
import { labelOf, lower, toNumber } from "@/lib/money";
import { currentPeriod, readAnchor, readCadence } from "@/lib/periods";
import { spentByCategory } from "@/lib/spending";
import { BudgetsEditor } from "./budgets-editor";

export const metadata = { title: "Budgets · FinanceBuddy" };

/** Income and transfers are not spending, so they get no budget line by default. */
const NOT_BUDGETED = new Set(["Income", "Transfer"]);

const monthKey = (value: number) => {
  const { year, month } = parts(value);
  return `${year}-${String(month).padStart(2, "0")}`;
};

export default async function BudgetsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const params = await searchParams;

  return withSheet("Budgets", (data) => {
    const cadence = readCadence(data.config);
    const period = currentPeriod(readAnchor(data.config), cadence, data.today);
    const requested = String(params.m ?? "");
    const month = /^\d{4}-\d{2}$/.test(requested) ? requested : monthKey(period.payday);
    const [y, m] = month.split("-").map(Number);
    const first = day(y, m, 1);

    let rows = data.budgets.filter((b) => lower(b.month) === month);
    let copiedFrom: string | null = null;
    if (!rows.length) {
      const months = [...new Set(data.budgets.map((b) => lower(b.month)).filter(Boolean))].sort();
      const earlier = months.filter((mm) => mm < month);
      copiedFrom = earlier.length ? earlier[earlier.length - 1] : (months[months.length - 1] ?? null);
      rows = copiedFrom ? data.budgets.filter((b) => lower(b.month) === copiedFrom) : [];
    }

    const amounts = new Map<string, number>();
    for (const row of rows) amounts.set(labelOf(row.category), (amounts.get(labelOf(row.category)) ?? 0) + toNumber(row.amount));
    const spent = Object.fromEntries(spentByCategory(data.transactions, period).map((r) => [r.category, r.spent]));

    const categories = [...new Set([
      ...CATEGORIES.filter((c) => !NOT_BUDGETED.has(c)),
      ...data.budgets.map((b) => labelOf(b.category)),
    ])];

    const link = "rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--panel)]";
    return (
      <div>
        <PageTitle caption="Monthly figures per category. The Payday page divides each one across the checks that month actually holds.">Budgets</PageTitle>
        <div className="mb-4 grid grid-cols-[auto_1fr_auto] items-center gap-3">
          <Link href={`/budgets?m=${monthKey(addMonths(first, -1))}`} className={link}>← {format(addMonths(first, -1), "Mon")}</Link>
          <div className="text-center font-semibold">{format(first, "Month YYYY")}</div>
          <Link href={`/budgets?m=${monthKey(addMonths(first, 1))}`} className={link}>{format(addMonths(first, 1), "Mon")} →</Link>
        </div>
        <BudgetsEditor
          key={month}
          month={month}
          cadence={cadence}
          copiedFrom={copiedFrom}
          entries={categories.map((category) => ({
            category,
            amount: amounts.has(category) ? amounts.get(category)! : null,
            spent: month === monthKey(period.payday) ? (spent[category] ?? 0) : 0,
          }))}
        />
      </div>
    );
  });
}
