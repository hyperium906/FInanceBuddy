/**
 * The Accounts page — balances in `_Accounts`, which the Savings page counts
 * as money saved. Typed by hand, or taken from a linked bank.
 */

import { withSheet } from "@/components/guard";
import { Callout, PageTitle } from "@/components/ui";
import { bankConfigured } from "@/lib/server/simplefin";
import { AccountsEditor } from "./accounts-editor";

export const metadata = { title: "Accounts · FinanceBuddy" };

export default async function AccountsPage() {
  return withSheet("Accounts", (data) => {
    const rows = data.accounts.filter((a) => a.id);
    return (
      <div>
        <PageTitle caption="Balances are written to _Accounts with a Last Updated stamp. Savings reads them as what has been put aside.">Accounts</PageTitle>
        {rows.length ? (
          <AccountsEditor
            bankConfigured={bankConfigured()}
            rows={rows.map((a) => ({ id: a.id, name: a.name, type: a.type, institution: a.institution, balance: a.balance, lastUpdated: a.lastUpdated }))}
          />
        ) : (
          <Callout>No accounts in <code>_Accounts</code> yet.</Callout>
        )}
      </div>
    );
  });
}
