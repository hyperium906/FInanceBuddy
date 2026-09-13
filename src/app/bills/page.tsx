/**
 * The Bills page — every recurring charge in `_Recurring`, when it next lands,
 * and what the statement says about it. Payday shows only the bills on the
 * check being viewed; this is the whole list, and where each one is edited.
 */

import { withSheet } from "@/components/guard";
import { Metric, Metrics, PageTitle, Section } from "@/components/ui";
import { byName, observe } from "@/lib/billing";
import { CATEGORIES } from "@/lib/categorize";
import { parts } from "@/lib/dates";
import { formatCurrency as fc } from "@/lib/money";
import { billedAmount, monthlyCost, nextOccurrence, normaliseFrequency, upcoming } from "@/lib/recurring";
import { BillsEditor, type BillRow } from "./bills-editor";

export const metadata = { title: "Bills · FinanceBuddy" };

export default async function BillsPage() {
  return withSheet("Bills", (data) => {
    const { today, recurring } = data;
    const seen = byName(observe(recurring, data.transactions, today));

    const rows: BillRow[] = recurring
      .filter((r) => r.name.trim())
      .map((r) => ({
        id: r.id,
        name: r.name,
        category: r.category,
        amount: Math.abs(r.amount),
        frequency: normaliseFrequency(r.frequency),
        dueDay: r.nextDue === null ? null : parts(r.nextDue).day,
        active: r.active,
        merchant: r.merchant,
        estimated: r.estimated,
        confirmed: r.confirmed,
        taxRate: r.taxRate,
        variable: r.variable,
        billed: billedAmount(r),
        next: r.active ? nextOccurrence(r.nextDue, r.frequency, today) : null,
        verdict: seen[r.name]?.verdict ?? null,
        note: seen[r.name]?.note ?? "",
      }))
      .sort((a, b) => Number(b.active) - Number(a.active) || (a.next ?? Infinity) - (b.next ?? Infinity) || a.name.localeCompare(b.name));

    const active = recurring.filter((r) => r.active);
    const monthly = active.reduce((s, r) => s + monthlyCost(billedAmount(r), r.frequency), 0);
    const soon = upcoming(recurring, today, 13);

    return (
      <div>
        <PageTitle caption="Every recurring charge, when it next lands, and whether the statement agrees. Click one to edit it — changes are written to _Recurring.">
          Bills &amp; subscriptions
        </PageTitle>
        <Metrics>
          <Metric label="A month" value={fc(monthly)} note={`${active.length} active`} />
          <Metric label="A year" value={fc(monthly * 12)} />
          <Metric label="Next 14 days" value={fc(soon.reduce((s, u) => s + u.amount, 0))} note={`${soon.length} charge(s)`} />
          <Metric label="Switched off" value={recurring.length - active.length} />
        </Metrics>
        <Section title="All bills">
          <BillsEditor rows={rows} categories={[...CATEGORIES]} today={today} />
        </Section>
      </div>
    );
  });
}
