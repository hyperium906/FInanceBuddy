"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { Field, inputClass, primaryButton, secondaryButton } from "@/components/form";
import { Callout, Caption, DataTable, Metric, Metrics, Section } from "@/components/ui";
import { UNCATEGORIZED } from "@/lib/categorize";
import { format, iso, parseDay, type Day } from "@/lib/dates";
import { formatCurrency as fc } from "@/lib/money";
import { periodContaining } from "@/lib/periods";
import { guessBankAccount, type BankAccount } from "@/lib/simplefin";
import { guessColumns, LOGGED_WINDOW_DAYS, readBankCsv, REQUIRED, type LedgerRow, type Mapping } from "@/lib/statements";
import { categorizeImport, commitImport, listBankAccounts, stageImport, syncFromBank, type StageResult } from "./actions";

type Staged = Extract<StageResult, { ok: true }>;
type Notice = { tone: "success" | "error" | "warn"; text: string } | null;

export interface SheetAccount {
  id: string;
  name: string;
  institution: string;
  type: string;
}

/**
 * Preview, then commit — deliberately. Rows arrive from an uploaded CSV or a
 * linked bank, and either way nothing reaches the sheet until the button is
 * pressed. Every category is editable first, because each correction becomes a
 * rule and the model is needed less with every import.
 */
export function Importer({ accounts, categories, anchor, cadence, bankConfigured, lastDates, today }: {
  accounts: SheetAccount[];
  categories: string[];
  anchor: Day | null;
  cadence: string;
  bankConfigured: boolean;
  lastDates: Record<string, Day | null>;
  today: Day;
}) {
  const ids = accounts.map((a) => a.id);
  const [source, setSource] = useState<"csv" | "bank">("csv");

  const [csv, setCsv] = useState("");
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [csvAccount, setCsvAccount] = useState(ids[0] ?? "");

  const [bankList, setBankList] = useState<BankAccount[] | null>(null);
  const [bankAccount, setBankAccount] = useState("");
  const [sheetAccount, setSheetAccount] = useState(ids[0] ?? "");
  const [startDate, setStartDate] = useState(iso(today - 30));

  const [staged, setStaged] = useState<Staged | null>(null);
  const [include, setInclude] = useState<boolean[]>([]);
  const [preview, setPreview] = useState<{ rows: LedgerRow[]; baseline: string[]; ruleHits: number; llmHits: number; unresolved: number; failures: string[] } | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [pending, startTransition] = useTransition();

  const raw = useMemo(() => (csv ? readBankCsv(csv) : null), [csv]);
  const distinct = mapping !== null && new Set(Object.values(mapping)).size === 3;

  function clearStage() {
    setStaged(null);
    setPreview(null);
    setInclude([]);
  }

  function stage(result: StageResult) {
    setPreview(null);
    if (result.ok) {
      setStaged(result);
      setInclude(result.maybe.map((m) => !m));
      setNotice(result.warnings.length ? { tone: "warn", text: result.warnings.join(" ") } : null);
    } else {
      clearStage();
      setNotice({ tone: "error", text: result.error });
    }
  }

  useEffect(() => {
    if (source !== "csv" || !raw || !mapping || !distinct) return;
    startTransition(async () => stage(await stageImport({ csv, mapping, accountId: csvAccount })));
  }, [source, csv, raw, mapping, distinct, csvAccount]);

  async function onFile(file: File | undefined) {
    setNotice(null);
    clearStage();
    if (!file) return;
    const text = await file.text();
    const parsed = readBankCsv(text);
    if (!parsed.rows.length) {
      setNotice({ tone: "warn", text: "That file has no rows in it." });
      return;
    }
    setMapping(guessColumns(parsed));
    setCsv(text);
  }

  const defaultStart = (accountId: string) => iso((lastDates[accountId] ?? today - 23) - 7);

  function pickBank(id: string, list: BankAccount[]) {
    setBankAccount(id);
    const match = accounts.find((a) => guessBankAccount(a, list) === id)?.id ?? ids[0] ?? "";
    setSheetAccount(match);
    setStartDate(defaultStart(match));
    clearStage();
  }

  function chooseSource(next: "csv" | "bank") {
    setSource(next);
    setNotice(null);
    clearStage();
    if (next === "bank" && bankConfigured && bankList === null) {
      startTransition(async () => {
        const result = await listBankAccounts();
        if (!result.ok) {
          setNotice({ tone: "error", text: result.error });
          return;
        }
        setBankList(result.accounts);
        if (result.warnings.length) setNotice({ tone: "warn", text: result.warnings.join(" ") });
        if (result.accounts.length) pickBank(result.accounts[0].id, result.accounts);
      });
    }
  }

  function fetchFromBank() {
    startTransition(async () =>
      stage(await syncFromBank({ bankAccountId: bankAccount, accountId: sheetAccount, startDay: parseDay(startDate) ?? today - 30 })),
    );
  }

  function categorize() {
    if (!staged) return;
    const rows = staged.fresh.filter((_, i) => include[i]);
    if (!rows.length) {
      setNotice({ tone: "warn", text: "Nothing is ticked to import." });
      return;
    }
    startTransition(async () => {
      const result = await categorizeImport(rows);
      if (!result.ok) {
        setNotice({ tone: "error", text: result.error });
        return;
      }
      setPreview({ ...result, baseline: result.rows.map((r) => r.category) });
    });
  }

  function commit() {
    if (!preview) return;
    startTransition(async () => {
      const result = await commitImport({ rows: preview.rows, baseline: preview.baseline });
      setNotice({ tone: result.ok ? "success" : "error", text: result.message });
      if (result.ok) {
        setCsv("");
        setMapping(null);
        clearStage();
      }
    });
  }

  const setCategory = (index: number, category: string) =>
    setPreview((p) => p && { ...p, rows: p.rows.map((r, i) => (i === index ? { ...r, category } : r)) });

  const breakdown = useMemo(() => {
    if (!preview || anchor === null) return [];
    const groups = new Map<string, { count: number; out: number }>();
    for (const row of preview.rows) {
      const label = row.date === null ? "—" : periodContaining(row.date, anchor, cadence).label;
      const group = groups.get(label) ?? { count: 0, out: 0 };
      group.count += 1;
      if (row.amount < 0) group.out += row.amount;
      groups.set(label, group);
    }
    return [...groups];
  }, [preview, anchor, cadence]);

  const maybeCount = staged?.maybe.filter(Boolean).length ?? 0;
  const ticked = include.filter(Boolean).length;
  const tab = (active: boolean) =>
    `rounded-md px-4 py-2 text-sm ${active ? "bg-[var(--accent)] text-white" : "border border-[var(--border)] hover:bg-[var(--panel)]"}`;

  return (
    <div className="space-y-4">
      <div className="flex gap-2" role="tablist">
        <button role="tab" aria-selected={source === "csv"} onClick={() => chooseSource("csv")} className={tab(source === "csv")}>Upload a CSV</button>
        <button role="tab" aria-selected={source === "bank"} onClick={() => chooseSource("bank")} className={tab(source === "bank")}>Sync from bank</button>
      </div>

      {notice && <Callout tone={notice.tone}>{notice.text}</Callout>}

      {source === "csv" && (
        <>
          <Field label="Bank CSV">
            <input type="file" accept=".csv,text/csv" onChange={(e) => onFile(e.target.files?.[0])} className={inputClass} key={csv ? "loaded" : "empty"} />
          </Field>
          {raw && mapping && (
            <Section title="Columns">
              <div className="grid gap-4 md:grid-cols-4">
                {REQUIRED.map((name) => (
                  <Field key={name} label={name}>
                    <select value={mapping[name]} onChange={(e) => setMapping({ ...mapping, [name]: e.target.value })} className={inputClass}>
                      {raw.columns.map((c) => <option key={c}>{c}</option>)}
                    </select>
                  </Field>
                ))}
                <Field label="Account this statement is from">
                  <select value={csvAccount} onChange={(e) => setCsvAccount(e.target.value)} className={inputClass}>
                    {ids.length ? ids.map((a) => <option key={a}>{a}</option>) : <option value="">(no accounts)</option>}
                  </select>
                </Field>
              </div>
              {!distinct && <Callout tone="error">Those need to be three different columns.</Callout>}
            </Section>
          )}
        </>
      )}

      {source === "bank" && !bankConfigured && (
        <Callout>
          <strong>No bank is linked yet.</strong>
          <ol className="mt-2 list-decimal space-y-1 pl-5">
            <li>
              At <a className="underline" href="https://bridge.simplefin.org/simplefin/create" target="_blank" rel="noreferrer">SimpleFIN Bridge</a>, connect
              your banks and create a setup token for this app.
            </li>
            <li>In this project, run <code>npm run simplefin:claim -- &lt;token&gt;</code>. It stores the result in <code>.env</code>, never on screen.</li>
            <li>Restart the dev server. On Vercel, add <code>SIMPLEFIN_ACCESS_URL</code> to the project&apos;s environment variables.</li>
          </ol>
        </Callout>
      )}

      {source === "bank" && bankConfigured && bankList !== null && (
        bankList.length ? (
          <Section title="Which account">
            <div className="grid gap-4 md:grid-cols-3">
              <Field label="Bank account">
                <select value={bankAccount} onChange={(e) => pickBank(e.target.value, bankList)} className={inputClass}>
                  {bankList.map((b) => <option key={b.id} value={b.id}>{[b.institution, b.name].filter(Boolean).join(" · ")}</option>)}
                </select>
              </Field>
              <Field label="Goes into sheet account">
                <select value={sheetAccount} onChange={(e) => { setSheetAccount(e.target.value); setStartDate(defaultStart(e.target.value)); clearStage(); }} className={inputClass}>
                  {ids.map((a) => <option key={a}>{a}</option>)}
                </select>
              </Field>
              <Field label="From" hint="Defaults to a week before the last transaction in the sheet for that account.">
                <input type="date" value={startDate} max={iso(today)} onChange={(e) => { setStartDate(e.target.value); clearStage(); }} className={inputClass} />
              </Field>
            </div>
            <button onClick={fetchFromBank} disabled={pending || !bankAccount || !sheetAccount} className={primaryButton}>Fetch transactions</button>
          </Section>
        ) : (
          <Callout tone="warn">SimpleFIN is linked but reports no accounts. Check the connections at SimpleFIN Bridge.</Callout>
        )
      )}

      {pending && <Caption>Working…</Caption>}

      {staged && (
        <Section title="What arrived">
          <Metrics>
            <Metric label={staged.source === "bank" ? "Rows from the bank" : "Rows in the file"} value={staged.incoming} />
            <Metric label="New" value={staged.fresh.length} note={staged.alreadyHeld ? `${staged.alreadyHeld} already in the sheet` : undefined} />
            <Metric label="Covering" value={staged.first !== null && staged.last !== null ? `${format(staged.first, "DD Mon")}–${format(staged.last, "DD Mon")}` : "—"} />
            <Metric label="Money out" value={fc(staged.moneyOut)} />
          </Metrics>
          {staged.loggedByHand > 0 && (
            <Callout>
              <strong>{staged.loggedByHand} row(s) were skipped as the bank&apos;s line for something you logged by hand</strong> — same amount, within{" "}
              {LOGGED_WINDOW_DAYS} days. What you logged stays in the sheet.
            </Callout>
          )}
          {staged.repeatedInFile > 0 && (
            <Callout tone="warn">
              <strong>{staged.repeatedInFile} row(s) appear twice</strong> with the same date, amount and description. Two identical purchases on one day look
              exactly like a broken export, so only the first of each was kept — check them in your bank if that is not right.
            </Callout>
          )}
          {maybeCount > 0 && (
            <Callout tone="warn">
              <strong>{maybeCount} row(s) look like transactions already in your sheet</strong> under a different description — same amount, same account,
              within 3 days — so they are unticked. Tick any that are genuinely new.
            </Callout>
          )}
          {!staged.fresh.length ? (
            <Callout tone="success">Everything here is already in your sheet. Nothing to do — which is exactly what should happen when imports overlap.</Callout>
          ) : !preview ? (
            <>
              <DataTable
                head={["Import", "Date", "Description", "Amount", ""]}
                numeric={[3]}
                rows={staged.fresh.map((r, i) => [
                  <input key="t" type="checkbox" checked={include[i] ?? false} onChange={(e) => setInclude((all) => all.map((v, j) => (j === i ? e.target.checked : v)))} aria-label={`Import ${r.description}`} />,
                  r.date === null ? "—" : format(r.date, "DD Mon YYYY"),
                  r.description,
                  fc(r.amount),
                  staged.maybe[i] ? <span key="m" className="text-xs text-[var(--muted)]">maybe already in sheet</span> : "",
                ])}
              />
              <Caption>{ticked} of {staged.fresh.length} row(s) ticked. Press Categorize to continue.</Caption>
              <button onClick={categorize} disabled={pending || !ticked} className={primaryButton}>Categorize</button>
            </>
          ) : null}
        </Section>
      )}

      {preview && (
        <Section
          title="Check the categories"
          caption={
            <>
              <strong>{preview.ruleHits}</strong> matched a rule for free, <strong>{preview.llmHits}</strong> needed the model,{" "}
              <strong>{preview.unresolved}</strong> could not be decided. Every correction you make here is saved as a rule, so that merchant is free next time.
            </>
          }
        >
          {preview.failures.length > 0 && (
            <Callout tone="warn">The model could not categorize everything — rules still applied; set the rest by hand. {preview.failures.join(" ")}</Callout>
          )}
          <DataTable
            head={["Date", "Description", "Amount", "Category"]}
            numeric={[2]}
            rows={preview.rows.map((r, i) => [
              r.date === null ? "—" : format(r.date, "DD Mon"),
              r.description,
              fc(r.amount),
              <select key="c" value={r.category} onChange={(e) => setCategory(i, e.target.value)} className={`${inputClass} min-w-40`}>
                {[...categories, UNCATEGORIZED].map((c) => <option key={c}>{c}</option>)}
              </select>,
            ])}
          />
          {breakdown.length > 0 && (
            <Caption>
              These rows fall across{" "}
              {breakdown.map(([label, g], i) => (
                <span key={label}>
                  {i > 0 && ", "}
                  <strong>{label}</strong> ({g.count} rows, {fc(g.out)} out)
                </span>
              ))}
            </Caption>
          )}
          <div className="flex gap-3">
            <button onClick={commit} disabled={pending} className={primaryButton}>Commit {preview.rows.length} row(s) to the sheet</button>
            <button onClick={() => setPreview(null)} disabled={pending} className={secondaryButton}>Back</button>
          </div>
        </Section>
      )}
    </div>
  );
}
