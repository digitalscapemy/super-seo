// lib/integrations/google.ts — Google OAuth (PKCE) + service account + Search Console, server-only, no SDK.
// Env: GOOGLE_SEO_CLIENT_ID, GOOGLE_SEO_CLIENT_SECRET, GOOGLE_SEO_REDIRECT_URI, GOOGLE_SEO_SA_JSON (service account JSON string)
import "server-only";
import { createHash, createSign, randomBytes } from "node:crypto";

export const GOOGLE_SCOPES = [
  "openid", "email",
  "https://www.googleapis.com/auth/webmasters.readonly",
  "https://www.googleapis.com/auth/analytics.readonly",
];

const b64url = (b: Buffer | string) => Buffer.from(b).toString("base64url");

/** Step 1: returns the consent URL plus state/verifier to store in an httpOnly, signed, short-lived cookie. */
export function buildAuthUrl() {
  const state = b64url(randomBytes(32));
  const verifier = b64url(randomBytes(64));
  const challenge = b64url(createHash("sha256").update(verifier).digest());
  const url = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  url.search = new URLSearchParams({
    response_type: "code",
    client_id: process.env.GOOGLE_SEO_CLIENT_ID!,
    redirect_uri: process.env.GOOGLE_SEO_REDIRECT_URI!,
    scope: GOOGLE_SCOPES.join(" "),
    state, code_challenge: challenge, code_challenge_method: "S256",
    access_type: "offline", include_granted_scopes: "true", prompt: "consent",
  }).toString();
  return { url: url.toString(), state, verifier };
}

/** Step 2: exchange the code. Caller must have verified `state` against the cookie first. */
export async function exchangeCode(code: string, verifier: string) {
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code", code, code_verifier: verifier,
      client_id: process.env.GOOGLE_SEO_CLIENT_ID!, client_secret: process.env.GOOGLE_SEO_CLIENT_SECRET!,
      redirect_uri: process.env.GOOGLE_SEO_REDIRECT_URI!,
    }),
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) throw new Error(`token exchange failed: ${res.status}`);
  const t = await res.json() as { refresh_token?: string; scope?: string; id_token?: string };
  if (!t.scope?.includes("webmasters.readonly")) throw new Error("Search Console permission was not granted.");
  if (!t.refresh_token) throw new Error("No refresh token returned. Disconnect and connect again.");
  const email = t.id_token ? JSON.parse(Buffer.from(t.id_token.split(".")[1], "base64url").toString()).email as string : null;
  return { refreshToken: t.refresh_token, scopes: t.scope, email };
}

export class ReconnectRequired extends Error {}

/** Access token from a stored (decrypted) refresh token. Cache it per integration for ~50 minutes. */
export async function accessTokenFromRefresh(refreshToken: string): Promise<string> {
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token", refresh_token: refreshToken,
      client_id: process.env.GOOGLE_SEO_CLIENT_ID!, client_secret: process.env.GOOGLE_SEO_CLIENT_SECRET!,
    }),
    signal: AbortSignal.timeout(15_000),
  });
  if (res.status === 400 && (await res.clone().json()).error === "invalid_grant") throw new ReconnectRequired();
  if (!res.ok) throw new Error(`refresh failed: ${res.status}`);
  return (await res.json()).access_token;
}

/** Platform mode: service-account JWT -> access token. */
export async function accessTokenFromServiceAccount(): Promise<string> {
  const sa = JSON.parse(process.env.GOOGLE_SEO_SA_JSON!) as { client_email: string; private_key: string };
  const now = Math.floor(Date.now() / 1000);
  const unsigned = `${b64url(JSON.stringify({ alg: "RS256", typ: "JWT" }))}.${b64url(JSON.stringify({
    iss: sa.client_email, aud: "https://oauth2.googleapis.com/token", iat: now, exp: now + 3600,
    scope: "https://www.googleapis.com/auth/webmasters.readonly https://www.googleapis.com/auth/analytics.readonly",
  }))}`;
  const sig = createSign("RSA-SHA256").update(unsigned).sign(sa.private_key);
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer", assertion: `${unsigned}.${b64url(sig)}` }),
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) throw new Error(`service account token failed: ${res.status}`);
  return (await res.json()).access_token;
}

async function gfetch(token: string, url: string, body?: unknown, attempt = 0): Promise<any> {
  const res = await fetch(url, {
    method: body ? "POST" : "GET",
    headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(60_000),
  });
  if ([429, 500, 502, 503, 504].includes(res.status) && attempt < 5) {
    await new Promise((r) => setTimeout(r, 2 ** attempt * 1000 + Math.random() * 500));
    return gfetch(token, url, body, attempt + 1);
  }
  if (!res.ok) throw new Error(`Google API ${res.status}`);
  return res.json();
}

const site = (property: string) => encodeURIComponent(property); // sc-domain:x.com -> sc-domain%3Ax.com

export const searchConsole = {
  sites: async (token: string) =>
    ((await gfetch(token, "https://www.googleapis.com/webmasters/v3/sites")).siteEntry ?? [])
      .filter((s: any) => s.permissionLevel !== "siteUnverifiedUser") as { siteUrl: string; permissionLevel: string }[],

  /** Paginates 25k rows at a time. Data lags ~2-3 days; use dataState "final" for stored history. */
  async *query(token: string, property: string, startDate: string, endDate: string, dimensions: string[], dataState = "final") {
    for (let startRow = 0; ; startRow += 25_000) {
      const { rows = [] } = await gfetch(token, `https://www.googleapis.com/webmasters/v3/sites/${site(property)}/searchAnalytics/query`,
        { startDate, endDate, dimensions, type: "web", dataState, rowLimit: 25_000, startRow });
      yield* rows as { keys: string[]; clicks: number; impressions: number; ctr: number; position: number }[];
      if (rows.length < 25_000) break;
    }
  },

  sitemaps: async (token: string, property: string) =>
    (await gfetch(token, `https://www.googleapis.com/webmasters/v3/sites/${site(property)}/sitemaps`)).sitemap ?? [],

  /** 2,000/day/property — key URLs only, cache results. */
  inspect: async (token: string, property: string, url: string) =>
    (await gfetch(token, "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
      { inspectionUrl: url, siteUrl: property })).inspectionResult?.indexStatusResult ?? {},
};

/** Exact host match only; never let a tenant attach a parent-domain property (it would expose other tenants). */
export function propertyMatchesDomains(property: string, verifiedDomains: string[]): boolean {
  const host = (property.startsWith("sc-domain:") ? property.slice(10) : (() => { try { return new URL(property).hostname; } catch { return ""; } })())
    .toLowerCase().replace(/^www\./, "");
  return !!host && verifiedDomains.some((d) => d.toLowerCase().replace(/^www\./, "") === host);
}
