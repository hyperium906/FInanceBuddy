/**
 * The SimpleFIN Bridge client.
 *
 * The Access URL is a bank credential — read-only, but it reads every linked
 * account — so it lives in an environment variable (SIMPLEFIN_ACCESS_URL),
 * never in the sheet, and never in the browser. `npm run simplefin:claim`
 * turns a one-time setup token into it.
 */

import type { SfinAccountSet } from "../simplefin";

export class BankError extends Error {
  name = "BankError";
}

export const bankConfigured = () => Boolean((process.env.SIMPLEFIN_ACCESS_URL ?? "").trim());

function access(): { base: string; authorization: string } {
  const raw = (process.env.SIMPLEFIN_ACCESS_URL ?? "").trim();
  if (!raw) throw new BankError("No bank is linked. Run `npm run simplefin:claim -- <token>` with a token from SimpleFIN Bridge.");
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new BankError("SIMPLEFIN_ACCESS_URL is not a URL. Claim a fresh token.");
  }
  if (url.protocol !== "https:") throw new BankError("SIMPLEFIN_ACCESS_URL must be https.");
  if (!url.username) throw new BankError("SIMPLEFIN_ACCESS_URL has no credentials in it. Claim a fresh token.");
  // fetch refuses URLs with credentials in them, so they travel as a header.
  const credentials = `${decodeURIComponent(url.username)}:${decodeURIComponent(url.password)}`;
  return {
    base: `${url.protocol}//${url.host}${url.pathname}`.replace(/\/$/, ""),
    authorization: `Basic ${Buffer.from(credentials).toString("base64")}`,
  };
}

export async function fetchAccountSet(options: {
  startDate?: number;
  accounts?: string[];
  balancesOnly?: boolean;
} = {}): Promise<SfinAccountSet> {
  const { base, authorization } = access();
  const url = new URL(`${base}/accounts`);
  if (options.startDate) url.searchParams.set("start-date", String(Math.floor(options.startDate)));
  for (const account of options.accounts ?? []) url.searchParams.append("account", account);
  if (options.balancesOnly) url.searchParams.set("balances-only", "1");

  let response: Response;
  try {
    response = await fetch(url, {
      headers: { Authorization: authorization, Accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });
  } catch (error) {
    throw new BankError(`Could not reach SimpleFIN: ${(error as Error).name === "TimeoutError" ? "it did not answer within a minute" : (error as Error).message}.`);
  }

  if (response.status === 403) {
    throw new BankError("SimpleFIN refused the access URL (403). Access may have been revoked — make a new token at SimpleFIN Bridge and claim it again.");
  }
  if (response.status === 402) throw new BankError("SimpleFIN says payment is required (402). Check your SimpleFIN Bridge subscription.");
  if (!response.ok) throw new BankError(`SimpleFIN returned HTTP ${response.status}.`);

  const body = (await response.json().catch(() => null)) as SfinAccountSet | null;
  if (!body || !Array.isArray(body.accounts)) throw new BankError("SimpleFIN sent something that is not an account set.");
  return body;
}
