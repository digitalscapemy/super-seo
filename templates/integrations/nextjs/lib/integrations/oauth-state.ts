// lib/integrations/oauth-state.ts — tamper-proof OAuth flow state in an HMAC-signed __Host- cookie.
// Env: SEO_OAUTH_STATE_SECRET (>= 32 random bytes, e.g. `openssl rand -base64 48`).
import "server-only";
import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

// __Host- prefix: browser only accepts it with Secure, Path=/ and no Domain, so a subdomain cannot set or overwrite it.
const COOKIE = "__Host-google_seo_oauth";
const MAX_AGE_S = 600;

export type OAuthFlowState = { state: string; verifier: string; tenantId: string | null; userId: string; exp: number };

function sign(payload: string): string {
  const secret = process.env.SEO_OAUTH_STATE_SECRET;
  if (!secret || secret.length < 32) throw new Error("SEO_OAUTH_STATE_SECRET is missing or too short");
  return createHmac("sha256", secret).update(payload).digest("base64url");
}

export async function saveFlowState(s: Omit<OAuthFlowState, "exp">) {
  const payload = Buffer.from(JSON.stringify({ ...s, exp: Date.now() + MAX_AGE_S * 1000 })).toString("base64url");
  (await cookies()).set(COOKIE, `${payload}.${sign(payload)}`, {
    httpOnly: true, secure: true, sameSite: "lax", path: "/", maxAge: MAX_AGE_S,
  });
}

/** Reads, verifies (signature + expiry) and deletes the flow state. Returns null if anything is off. */
export async function takeFlowState(): Promise<OAuthFlowState | null> {
  const jar = await cookies();
  const raw = jar.get(COOKIE)?.value;
  jar.delete(COOKIE); // single use
  if (!raw) return null;
  const [payload, mac] = raw.split(".");
  if (!payload || !mac) return null;
  const expected = Buffer.from(sign(payload));
  const given = Buffer.from(mac);
  if (expected.length !== given.length || !timingSafeEqual(expected, given)) return null;
  const s = JSON.parse(Buffer.from(payload, "base64url").toString()) as OAuthFlowState;
  return s.exp > Date.now() ? s : null;
}
