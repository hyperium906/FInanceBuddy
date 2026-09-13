#!/usr/bin/env node
/**
 * Turn a SimpleFIN setup token into an Access URL, and store it in .env.
 *
 *   npm run simplefin:claim -- <token>
 *
 * Get a token at https://bridge.simplefin.org/simplefin/create after linking
 * your banks there. Tokens are one-time: claiming consumes it. The Access URL
 * is a credential, so it is written to .env and never printed.
 */

import { existsSync, readFileSync, writeFileSync } from "node:fs";

const token = (process.argv[2] ?? "").trim();
if (!token) {
  console.error("Usage: npm run simplefin:claim -- <SimpleFIN setup token>");
  process.exit(1);
}

let claimUrl;
try {
  claimUrl = new URL(Buffer.from(token, "base64").toString("utf-8").trim());
} catch {
  console.error("That does not look like a SimpleFIN token (it should be base64 of a URL). Copy it again from SimpleFIN Bridge.");
  process.exit(1);
}
if (claimUrl.protocol !== "https:") {
  console.error("The token points at a non-https URL. Refusing to send it anywhere.");
  process.exit(1);
}

const response = await fetch(claimUrl, { method: "POST" });
if (response.status === 403) {
  console.error(
    "SimpleFIN refused the token (403): it does not exist or has already been claimed.\n" +
      "If you did not claim it yourself, someone else may have — disable that token at SimpleFIN Bridge and make a new one.",
  );
  process.exit(1);
}
if (!response.ok) {
  console.error(`Claiming failed with HTTP ${response.status}. The token may still be unused; try again shortly.`);
  process.exit(1);
}

const access = (await response.text()).trim();
let parsed;
try {
  parsed = new URL(access);
} catch {
  console.error("SimpleFIN answered with something that is not an Access URL. Nothing was written.");
  process.exit(1);
}
if (parsed.protocol !== "https:" || !parsed.username) {
  console.error("The Access URL is not https or carries no credentials. Nothing was written.");
  process.exit(1);
}

const path = ".env";
const lines = existsSync(path) ? readFileSync(path, "utf-8").split("\n") : [];
const entry = `SIMPLEFIN_ACCESS_URL=${access}`;
const at = lines.findIndex((line) => line.startsWith("SIMPLEFIN_ACCESS_URL="));
if (at >= 0) lines[at] = entry;
else lines.push(entry);
writeFileSync(path, `${lines.join("\n").replace(/\n+$/, "")}\n`, { mode: 0o600 });

console.log(`Claimed. SIMPLEFIN_ACCESS_URL is saved in ${path} (not shown here).`);
console.log("Restart `npm run dev` to pick it up. For Vercel, add the same variable in the project's environment settings.");
