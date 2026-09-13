/**
 * The Transactions page — `_Transactions` for one pay period, with category,
 * notes and account editable in place. A corrected category also teaches a
 * rule, so the same merchant is right on the next import.
 */

import { withSheet } from "@/components/guard";
import { PeriodNav } from "@/components/period-nav";
import { Callout, PageTitle } from "@/components/ui";
import { CATEGORIES } from "@/lib/categorize";
import { currentPeriod, readAnchor, readCadence, shift } from "@/lib/periods";
import { TransactionsEditor } from "./transactions-editor";

export const metadata = { title: "Transactions · FinanceBuddy" };

export default async function TransactionsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const params = await searchParams;
  const offset = Math.min(Number.parseInt(String(params.p ?? "0"), 10) || 0, 0);

  return withSheet("Transactions", (data) => {
    const period = shift(currentPeriod(readAnchor(data.config), readCadence(data.config), data.today), offset);
    const inPeriod = data.transactions.filter((t) => t.date !== null && period.contains(t.date)).sort((a, b) => b.date! - a.date!);
    const withoutId = inPeriod.filter((t) => !t.id).length;

    return (
      <div>
        <PageTitle caption="Category, notes and account are editable. Date, amount and description stay locked, so the sheet keeps agreeing with the bank.">
          Transactions
        </PageTitle>
        <PeriodNav period={period} offset={offset} today={data.today} path="/transactions" />
        <div className="mt-4 space-y-3">
          {withoutId > 0 && <Callout tone="warn">{withoutId} row(s) in this period have no Transaction ID and can only be edited in the sheet.</Callout>}
          <TransactionsEditor
            key={period.start}
            rows={inPeriod.filter((t) => t.id).map((t) => ({ id: t.id, date: t.date, description: t.description, amount: t.amount, category: t.category, notes: t.notes, accountId: t.accountId }))}
            categories={[...CATEGORIES]}
            accounts={data.accounts.map((a) => a.id).filter(Boolean)}
          />
        </div>
      </div>
    );
  });
}
