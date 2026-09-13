/**
 * Application configuration, from environment variables.
 *
 * Locally these come from `.env` (Next.js loads it); deployed, from the Vercel
 * project's environment variables. Nothing secret and no model name is
 * hardcoded here, and every missing variable is reported at once.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

export class ConfigError extends Error {
  name = "ConfigError";
}

export interface ServiceAccount {
  client_email: string;
  private_key: string;
}

export interface SheetConfig {
  sheetId: string;
  credentials: ServiceAccount;
  /** Where the key came from, for error messages. Never the key itself. */
  credsSource: string;
}

export interface GeminiConfig {
  apiKey: string;
  model: string;
  modelFast: string;
}

const setting = (name: string) => (process.env[name] ?? "").trim();

function missing(names: string[]): never {
  throw new ConfigError(
    `Missing required configuration: ${names.join(", ")}. Set them in .env locally, or in the Vercel project's environment variables.`,
  );
}

/**
 * Parse an inline service-account key, as raw JSON or base64. Hosts differ in
 * how happily they carry a multi-line value, so base64 survives the trip.
 */
function decodeCreds(raw: string): ServiceAccount {
  let info: unknown;
  try {
    info = JSON.parse(raw);
  } catch (error) {
    try {
      info = JSON.parse(Buffer.from(raw, "base64").toString("utf-8"));
    } catch {
      if (raw.trimStart().startsWith("{")) {
        throw new ConfigError(`GOOGLE_CREDS_JSON is not valid JSON: ${(error as Error).message}. Paste the key file exactly as downloaded.`);
      }
      throw new ConfigError("GOOGLE_CREDS_JSON is neither JSON nor base64-encoded JSON. Paste the whole key file, braces included — not the path to it.");
    }
  }
  return validate(info, "GOOGLE_CREDS_JSON");
}

function validate(info: unknown, source: string): ServiceAccount {
  if (!info || typeof info !== "object" || Array.isArray(info)) {
    throw new ConfigError(`${source} must be a JSON object.`);
  }
  const record = info as Record<string, unknown>;
  const absent = ["client_email", "private_key"].filter((key) => !record[key]);
  if (absent.length) {
    throw new ConfigError(
      `${source} is missing ${absent.join(", ")}. That is not a service-account key — download one from Google Cloud → Credentials → Service account → Keys.`,
    );
  }
  return { client_email: String(record.client_email), private_key: String(record.private_key) };
}

let sheetConfig: SheetConfig | null = null;

export function getSheetConfig(): SheetConfig {
  if (sheetConfig) return sheetConfig;
  const sheetId = setting("GOOGLE_SHEET_ID");
  const inline = setting("GOOGLE_CREDS_JSON");
  const credsPath = setting("GOOGLE_CREDS_PATH");

  if (!sheetId) missing(["GOOGLE_SHEET_ID"]);

  let credentials: ServiceAccount;
  let credsSource: string;
  if (inline) {
    credentials = decodeCreds(inline);
    credsSource = "GOOGLE_CREDS_JSON";
  } else if (credsPath) {
    let text: string;
    try {
      // A local-development path chosen at runtime; nothing for the bundler to trace.
      text = readFileSync(path.resolve(/*turbopackIgnore: true*/ process.cwd(), credsPath), "utf-8");
    } catch {
      throw new ConfigError(`GOOGLE_CREDS_PATH points at ${credsPath}, which could not be read.`);
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new ConfigError(`The key file at GOOGLE_CREDS_PATH (${credsPath}) is not valid JSON.`);
    }
    credentials = validate(parsed, "GOOGLE_CREDS_PATH");
    credsSource = "GOOGLE_CREDS_PATH";
  } else {
    throw new ConfigError(
      "No Google service-account key configured. Set GOOGLE_CREDS_JSON to the key file's contents (the usual choice on Vercel, " +
        "where the gitignored file does not exist), or GOOGLE_CREDS_PATH to its location on disk.",
    );
  }

  sheetConfig = { sheetId, credentials, credsSource: `${credsSource} (${credentials.client_email})` };
  return sheetConfig;
}

/** Only the Import page needs the model, so only it pays for this being unset. */
export function getGeminiConfig(): GeminiConfig {
  const names = ["GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_MODEL_FAST"];
  const absent = names.filter((name) => !setting(name));
  if (absent.length) missing(absent);
  return { apiKey: setting("GEMINI_API_KEY"), model: setting("GEMINI_MODEL"), modelFast: setting("GEMINI_MODEL_FAST") };
}
