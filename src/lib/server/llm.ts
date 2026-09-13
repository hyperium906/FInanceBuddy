/**
 * The only module that talks to an LLM provider.
 *
 * Everything else calls `generate`. Swapping providers means rewriting this
 * file and nothing else, so provider types stay inside it — callers see
 * strings, parsed JSON, and `LLMError`. Model names come from configuration.
 */

import { ApiError, GoogleGenAI } from "@google/genai";
import { getGeminiConfig } from "./config";

/** Backoff after each rate-limited attempt. Five attempts in all. */
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000];
const RETRY_STATUSES = new Set([429, 500, 502, 503, 504]);

export class LLMError extends Error {
  name = "LLMError";
}

export class RateLimited extends LLMError {
  name = "RateLimited";
}

let client: GoogleGenAI | null = null;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Send `prompt` and return the answer: parsed JSON when `schema` is given,
 * text otherwise. Rate limits and transient 5xx are retried with backoff.
 */
export async function generate(
  prompt: string,
  { schema, model, temperature = 0 }: { schema?: unknown; model?: string; temperature?: number } = {},
): Promise<unknown> {
  const config = getGeminiConfig();
  client ??= new GoogleGenAI({ apiKey: config.apiKey });
  const target = model || config.model;

  let last: unknown = null;
  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
    try {
      const response = await client.models.generateContent({
        model: target,
        contents: prompt,
        config: {
          temperature,
          ...(schema ? { responseMimeType: "application/json", responseJsonSchema: schema } : {}),
        },
      });
      return unpack(response.text, Boolean(schema));
    } catch (error) {
      last = error;
      const status = error instanceof ApiError ? error.status : undefined;
      if (status === undefined || !RETRY_STATUSES.has(status) || attempt === RETRY_DELAYS_MS.length) break;
      await sleep(RETRY_DELAYS_MS[attempt]);
    }
  }

  if (last instanceof LLMError) throw last;
  if (last instanceof ApiError && last.status === 429) {
    throw new RateLimited(
      `${target} is rate limited and did not recover after ${RETRY_DELAYS_MS.length} retries. Wait a moment and try again.`,
    );
  }
  throw new LLMError(`Call to ${target} failed: ${(last as Error)?.message ?? last}`);
}

function unpack(text: string | undefined, json: boolean): unknown {
  const body = (text ?? "").trim();
  if (!body) throw new LLMError(json ? "Model returned an empty response where JSON was required." : "Model returned an empty response.");
  if (!json) return body;
  try {
    return JSON.parse(body);
  } catch {
    throw new LLMError(`Model did not return valid JSON despite a schema: ${body.slice(0, 200)}`);
  }
}
