/**
 * The Import page — getting a payday's transactions into the sheet, from an
 * uploaded CSV or a linked bank.
 *
 * What makes it safe to run every payday is the deduplication: consecutive
 * imports overlap, and matching on date, amount and description means a row
 * whose category was fixed by hand is still recognised as one already held.
 */

import { withSheet } from "@/components/guard";
import { Caption, PageTitle } from "@/components/ui";
import { CATEGORIES } from "@/lib/categorize";
import { format, type Day } from "@/lib/dates";
import { readAnchor, readCadence } from "@/lib/periods";
import { bankConfigured } from "@/lib/server/simplefin";
import { span } from "@/lib/statements";
import { Importer } from "./importer";

export const metadata = { title: "Import · FinanceBuddy" };

/** Categorizing a long statement can wait out several rate-limit backoffs. */
export const maxDuration = 300;

export default async function ImportPage() {
  return withSheet("Import a statement", (data) => {
    let anchor: Day | null = null;
    try {
      anchor = readAnchor(data.config);
    } catch {
      // The period breakdown is a nicety; an import does not need a pay schedule.
    }
    const [first, last] = span(data.transactions);

    const lastDates: Record<string, Day | null> = {};
    for (const t of data.transactions) {
      if (t.date !== null && t.accountId) lastDates[t.accountId] = Math.max(lastDates[t.accountId] ?? t.date, t.date);
    }

    return (
      <div>
        <PageTitle caption="Upload your bank's CSV, or sync a linked bank. Overlap with what is already imported is safe: anything already in the sheet is skipped.">
          Import transactions
        </PageTitle>
        <div className="mb-4">
          <Caption>
            {first !== null && last !== null ? (
              <>
                Your sheet holds <strong>{data.transactions.length}</strong> transactions, {format(first, "DD Mon YYYY")} to{" "}
                <strong>{format(last, "DD Mon YYYY")}</strong>. Import from that date onward — overlapping days are skipped automatically.
              </>
            ) : (
              "Your sheet has no transactions yet. Import your first statement."
            )}
          </Caption>
          {!data.hasRulesTab && (
            <Caption>
              Corrections are remembered in a <code>_Rules</code> tab (headers <code>Match</code>, <code>Category</code>). It does not exist yet, so
              corrections will only apply to this import.
            </Caption>
          )}
        </div>
        <Importer
          accounts={data.accounts.filter((a) => a.id).map((a) => ({ id: a.id, name: a.name, institution: a.institution, type: a.type }))}
          categories={[...CATEGORIES]}
          anchor={anchor}
          cadence={readCadence(data.config)}
          bankConfigured={bankConfigured()}
          lastDates={lastDates}
          today={data.today}
        />
      </div>
    );
  });
}
