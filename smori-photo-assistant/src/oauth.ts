/**
 * Shopify OAuth authorization code grant (non-embedded app, offline token).
 *
 *   GET /auth?shop=x.myshopify.com   -> redirect to Shopify's consent screen
 *   GET /auth/callback               -> verify state + hmac, exchange code, store token
 */
import crypto from 'node:crypto';
import type { Request, Response } from 'express';
import { config } from './config.js';
import { saveSession } from './tokens.js';

const SHOP_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]*\.myshopify\.com$/;
const STATE_COOKIE = 'smori_oauth_state';

export function isValidShop(shop: unknown): shop is string {
  return typeof shop === 'string' && SHOP_RE.test(shop);
}

export function oauthConfigured(): boolean {
  return Boolean(config.apiKey && config.apiSecret && config.appUrl && config.sessionSecret);
}

/** HMAC of a Shopify query string (callback and admin entry): drop `hmac`, sort, join with &. */
export function verifyQueryHmac(query: Record<string, unknown>, secret = config.apiSecret): boolean {
  const { hmac, ...rest } = query as Record<string, string | string[]>;
  if (typeof hmac !== 'string') return false;
  const message = Object.keys(rest).sort().map((k) => `${k}=${Array.isArray(rest[k]) ? (rest[k] as string[]).join(',') : rest[k]}`).join('&');
  const digest = crypto.createHmac('sha256', secret).update(message).digest('hex');
  return digest.length === hmac.length && crypto.timingSafeEqual(Buffer.from(digest), Buffer.from(hmac));
}

/** HMAC of a webhook body (base64 in X-Shopify-Hmac-Sha256). */
export function verifyWebhookHmac(rawBody: Buffer, header: string | undefined, secret = config.apiSecret): boolean {
  if (!header) return false;
  const digest = crypto.createHmac('sha256', secret).update(rawBody).digest('base64');
  return digest.length === header.length && crypto.timingSafeEqual(Buffer.from(digest), Buffer.from(header));
}

function sign(value: string): string {
  return crypto.createHmac('sha256', config.sessionSecret).update(value).digest('base64url');
}

function parseCookies(header: string | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of (header ?? '').split(';')) {
    const i = part.indexOf('=');
    if (i > 0) out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}

export function beginAuth(req: Request, res: Response): void {
  if (!oauthConfigured()) { res.status(500).send('OAuth is not configured (SHOPIFY_API_KEY / SHOPIFY_API_SECRET / SHOPIFY_APP_URL / SESSION_SECRET)'); return; }
  const shop = req.query.shop;
  if (!isValidShop(shop)) { res.status(400).send('missing or invalid shop parameter (expected something.myshopify.com)'); return; }
  const nonce = crypto.randomBytes(16).toString('hex');
  const payload = `${nonce}|${shop}`;
  res.cookie(STATE_COOKIE, `${payload}|${sign(payload)}`, { httpOnly: true, secure: config.nodeEnv === 'production', sameSite: 'lax', maxAge: 10 * 60 * 1000, path: '/auth' });
  const url = new URL(`https://${shop}/admin/oauth/authorize`);
  url.searchParams.set('client_id', config.apiKey);
  url.searchParams.set('scope', config.scopes);
  url.searchParams.set('redirect_uri', `${config.appUrl}/auth/callback`);
  url.searchParams.set('state', nonce);
  res.redirect(url.toString());
}

export async function exchangeCode(shop: string, code: string, fetchImpl: typeof fetch = fetch): Promise<{ access_token: string; scope: string }> {
  const res = await fetchImpl(`https://${shop}/admin/oauth/access_token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ client_id: config.apiKey, client_secret: config.apiSecret, code }),
  });
  if (!res.ok) throw new Error(`token exchange failed: HTTP ${res.status} ${(await res.text()).slice(0, 300)}`);
  const json = (await res.json()) as { access_token?: string; scope?: string };
  if (!json.access_token) throw new Error('token exchange returned no access_token');
  return { access_token: json.access_token, scope: json.scope ?? '' };
}

export async function handleCallback(req: Request, res: Response, fetchImpl: typeof fetch = fetch): Promise<void> {
  const { shop, code, state } = req.query as Record<string, string>;
  if (!isValidShop(shop) || typeof code !== 'string' || typeof state !== 'string') { res.status(400).send('bad callback parameters'); return; }
  if (!verifyQueryHmac(req.query as Record<string, unknown>)) { res.status(400).send('hmac verification failed'); return; }
  const cookie = parseCookies(req.headers.cookie)[STATE_COOKIE] ?? '';
  const [nonce, cookieShop, sig] = cookie.split('|');
  const payload = `${nonce}|${cookieShop}`;
  const sigOk = Boolean(sig) && sig.length === sign(payload).length && crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(sign(payload)));
  if (!sigOk || nonce !== state || cookieShop !== shop) { res.status(400).send('state mismatch; start again from the install link'); return; }
  res.clearCookie(STATE_COOKIE, { path: '/auth' });

  const token = await exchangeCode(shop, code, fetchImpl);
  const granted = new Set(token.scope.split(',').map((s) => s.trim()));
  const missing = config.scopes.split(',').filter((s) => !granted.has(s) && !granted.has(s.replace(/^read_/, 'write_')));
  await saveSession({ shop, accessToken: token.access_token, scope: token.scope });
  if (missing.length) { res.status(200).send(`Installed, but these scopes were not granted: ${missing.join(', ')}. Re-run the install link after fixing the app configuration.`); return; }
  res.redirect(`/?shop=${encodeURIComponent(shop)}&installed=1`);
}
