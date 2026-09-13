/**
 * A single shared password in front of the whole app.
 *
 * The app reads balances and writes to the spreadsheet, and a Vercel URL is
 * public. The session cookie holds an HMAC of a fixed label keyed by
 * APP_PASSWORD, so changing the password signs everyone out, and nothing
 * secret is stored in the browser. Web Crypto, so it runs in the proxy too.
 */

export const SESSION_COOKIE = "fb_session";
export const SESSION_DAYS = 30;

export async function sessionToken(password: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode("financebuddy-session-v1"));
  return [...new Uint8Array(signature)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Equal-length comparison that does not stop at the first differing character. */
export function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** Only same-site paths, so a crafted `?next=` cannot bounce a login elsewhere. */
export function safeNext(next: unknown): string {
  const path = String(next ?? "");
  return path.startsWith("/") && !path.startsWith("//") && !path.startsWith("/login") ? path : "/payday";
}
