/**
 * The Log page — money that has moved but is not in a bank export yet.
 *
 * A tithe given today does not reach a statement for days, and waiting for the
 * next upload means Payday shows it as still to do all that time. Logging it
 * writes the row now; when the export arrives, Import recognises the bank's
 * line for it and skips it rather than counting it twice.
 */

import { withSheet } from "@/components/guard";
import { DataTable, PageTitle, Section } from "@/components/ui";
import { CATEGORIES } from "@/lib/categorize";
import { format, iso } from "@/lib/dates";
import { formatCurrency as fc } from "@/lib/money";
import { LOGGED_WINDOW_DAYS, loggedByHand } from "@/lib/statements";
import { LogForm } from "./log-form";

export const metadata = { title: "Log · FinanceBuddy" };

export default async function LogPage() {
  return withSheet("Log a transaction", (data) => {
    const accounts = data.accounts.map((a) => a.id).filter(Boolean);
    const checking = data.accounts.filter((a) => a.type.toLowerCase() === "checking");
    const defaultAccount = checking.length ? checking[checking.length - 1].id : (accounts[0] ?? "");
    const logged = loggedByHand(data.transactions)
      .filter((t) => t.date !== null)
      .sort((a, b) => b.date! - a.date!)
      .slice(0, 10);

    return (
      <div>
        <PageTitle
          caption={`For money that has already moved but is not in a bank export yet — a tithe, cash, a transfer. When the statement arrives, Import skips the bank's line for it (same amount within ${LOGGED_WINDOW_DAYS} days), so it is never counted twice.`}
        >
          Log a transaction
        </PageTitle>
        <LogForm accounts={accounts} defaultAccount={defaultAccount} categories={[...CATEGORIES]} today={iso(data.today)} />
        {logged.length > 0 && (
          <Section title="Logged by hand">
            <DataTable
              head={["Date", "What", "Category", "Amount"]}
              numeric={[3]}
              rows={logged.map((t) => [format(t.date!, "Dow DD Mon"), t.description, t.category, fc(t.amount)])}
            />
          </Section>
        )}
      </div>
    );
  });
}
