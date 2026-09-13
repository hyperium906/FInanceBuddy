/**
 * The Savings page — where money is already going, and what a goal asks of it.
 *
 * A goal is not a new pot beside the existing allocations; it is a claim on
 * the same money. So the page opens with what is already leaving for savings
 * and measures a goal against that, not against an imaginary spare balance.
 */

import { CompositionBar } from "@/components/charts";
import { withSheet } from "@/components/guard";
import { Callout, Caption, DataTable, Meter, Metric, Metrics, PageTitle, Section, STATUS_COLOR, StatusChip } from "@/components/ui";
import { monthAhead } from "@/lib/commitments";
import { format } from "@/lib/dates";
import { formatCurrency as fc } from "@/lib/money";
import { perMonth, perPeriod, readAnchor, readCadence } from "@/lib/periods";
import {
  buckets, perCheck, planFor, readGoals, retirementRate, retirementTotal, savedTotal, savingsRate, tracked, untracked, type GoalPlan,
} from "@/lib/savings";
import { paycheckAmount } from "@/lib/server/data";
import { GoalForm } from "./goal-form";
import { VERDICT_STATUS, VERDICT_WORDS } from "./verdicts";

export const metadata = { title: "Savings · FinanceBuddy" };

export default async function SavingsPage() {
  return withSheet("Savings", (data) => {
    const { config, today } = data;
    const anchor = readAnchor(config);
    const cadence = readCadence(config);
    const paycheck = paycheckAmount(config);

    const pots = buckets(data.allocations, data.accounts, paycheck, cadence);
    // Averaged over the next four checks: the rent check is negative and the
    // one before it flush, and a plan built on either alone does not repeat.
    const plans = monthAhead(anchor, paycheck, data.recurring, data.allocations, cadence, today, 4);
    const spare = Math.max(plans.reduce((s, p) => s + p.left, 0) / plans.length, 0);

    const rate = savingsRate(pots);
    const missing = untracked(pots);
    const savings = pots.filter((b) => b.kind === "savings");
    const excluded = pots.filter((b) => b.kind === "debt" || b.kind === "giving");
    const locked = pots.filter((b) => b.kind === "retirement");

    const goals = readGoals(data.goals);
    const ratePerCheck = perPeriod(rate, cadence);

    return (
      <div>
        <PageTitle>Savings</PageTitle>
        <Metrics>
          <Metric label="Saved so far" value={fc(savedTotal(pots))} note={missing.length ? `${missing.length} bucket(s) untracked` : "all buckets tracked"} />
          <Metric label="Going in" value={`${fc(rate)}/mo`} note={`${fc(perPeriod(rate, cadence))} a check`} />
          <Metric label="Spare on top" value={`${fc(spare)}/check`} note={`${fc(perMonth(spare, cadence))}/mo`} />
          <Metric label="Retirement" value={fc(retirementTotal(pots))} note={`${fc(retirementRate(pots))}/mo — not reachable`} />
        </Metrics>
        {missing.length > 0 && (
          <div className="mt-3">
            <Caption>
              <strong>{missing.map((b) => b.name).join(", ")}</strong> have no row in <code>_Accounts</code>, so what they hold is unknown rather than
              zero — it is not counted above. Adding those accounts would fix the figure.
            </Caption>
          </div>
        )}

        <Section title="Where it goes each month">
          {savings.length > 0 && <CompositionBar items={savings.map((b) => ({ label: b.name, value: b.monthly }))} name="Monthly savings" />}
          <DataTable
            head={["Bucket", "Per month", "Per check", "Kind", "Balance"]}
            numeric={[1, 2, 4]}
            rows={pots.map((b) => [b.name, fc(b.monthly), fc(perCheck(b, cadence)), b.kind, tracked(b) ? fc(b.balance) : "not tracked"])}
          />
          {excluded.length > 0 && (
            <Caption>
              <strong>{excluded.map((b) => b.name).join(", ")}</strong> are left out of the savings rate: repayment and giving both leave and neither
              accumulates, so counting them would overstate what is being put aside.
            </Caption>
          )}
          {locked.length > 0 && (
            <Caption>
              <strong>{locked.map((b) => b.name).join(", ")}</strong> is saving, but not money a goal this year can draw on — it is counted on its own
              line above rather than in the rate a deadline is measured against.
            </Caption>
          )}
        </Section>

        <Section title="Goals">
          {!goals.length && (
            <Callout>
              No goals in <code>_Goals</code> yet. Set one below — the target is a number you can change, so it is worth putting a guess in to see what it
              would take.
            </Callout>
          )}
          {goals.map((goal) => (
            <OneGoal key={goal.name} plan={planFor(goal, anchor, spare, ratePerCheck, cadence, today)} />
          ))}
          <GoalForm
            goals={goals.map((g) => ({ name: g.name, target: g.target, saved: g.saved, deadline: g.deadline, bucket: g.bucket }))}
            anchor={anchor}
            cadence={cadence}
            spare={spare}
            ratePerCheck={ratePerCheck}
            today={today}
          />
        </Section>
      </div>
    );
  });
}

function OneGoal({ plan }: { plan: GoalPlan }) {
  const { goal } = plan;
  const status = VERDICT_STATUS[plan.verdict];
  return (
    <div className="space-y-2 rounded-lg border border-[var(--border)] p-4">
      <div className="grid gap-4 md:grid-cols-[3fr_2fr]">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="text-lg font-semibold">{goal.name}</h3>
            <StatusChip status={status}>{VERDICT_WORDS[plan.verdict]}</StatusChip>
          </div>
          <Meter share={goal.progress} color={STATUS_COLOR[status]} height={16} />
          <Caption>
            <strong>{fc(goal.saved)}</strong> of {fc(goal.target)} — {fc(goal.remaining)} to go
            {goal.deadline !== null && `, by ${format(goal.deadline, "DD Mon YYYY")}`}
          </Caption>
        </div>
        <div>
          {goal.met ? (
            <Callout tone="success">Funded.</Callout>
          ) : (
            <div className="space-y-1">
              <div className="text-sm text-[var(--muted)]">Needs per check</div>
              <div className="text-2xl font-semibold">{fc(plan.requiredPerCheck)}</div>
              <Caption>
                {plan.checksLeft ? `over ${plan.checksLeft} check(s)` : "no date set"} · spare per check: <strong>{fc(plan.availablePerCheck)}</strong>
              </Caption>
            </div>
          )}
        </div>
      </div>
      {(plan.verdict === "stretch" || plan.verdict === "missing") && (
        <>
          <Callout tone="warn">
            <strong>{fc(plan.shortfallPerCheck)} short per check.</strong> Landing this on time needs {fc(plan.requiredPerCheck)} from each of the{" "}
            {plan.checksLeft} remaining checks, and only {fc(plan.availablePerCheck)} is spare. Either the target comes down, the date moves out, or one of
            the standing transfers pauses.
          </Callout>
          {plan.lands !== null && (
            <Caption>
              At the {fc(plan.currentRatePerCheck)} a check already going into savings, it would land around <strong>{format(plan.lands, "Mon YYYY")}</strong> —{" "}
              {plan.checksAtCurrentRate} checks away.
            </Caption>
          )}
        </>
      )}
      {plan.verdict === "on-track" && plan.checksLeft > 0 && (
        <Caption>
          Setting aside {fc(plan.requiredPerCheck)} from each of the next {plan.checksLeft} checks lands it on time, with{" "}
          {fc(plan.availablePerCheck - plan.requiredPerCheck)} a check still spare.
        </Caption>
      )}
    </div>
  );
}
