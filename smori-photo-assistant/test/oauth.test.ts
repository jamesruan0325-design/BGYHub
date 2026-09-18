import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'smori-oauth-'));
process.env.DATA_DIR = tmp;
process.env.SESSION_SECRET = 'test-secret-test-secret-test-secret-1234';
process.env.SHOPIFY_API_KEY = 'client-id-123';
process.env.SHOPIFY_API_SECRET = 'shpss_secret_abc';
process.env.SHOPIFY_APP_URL = 'https://app.example.com';
process.env.SHOPIFY_SHOP = 'smori-9216.myshopify.com';
delete process.env.SHOPIFY_ADMIN_TOKEN;

const { saveSession, getSession, deleteSession, listShops } = await import('../src/tokens.js');
const { beginAuth, handleCallback, verifyQueryHmac, verifyWebhookHmac, isValidShop } = await import('../src/oauth.js');
const { resolveSession } = await import('../src/shopify.js');
const { handleWebhook } = await import('../src/webhooks.js');

after(async () => { await fs.rm(tmp, { recursive: true, force: true }); });

function fakeRes() {
  const r: any = { statusCode: 200, headers: {} as Record<string, string>, cookies: {} as Record<string, string>, body: '', redirectedTo: '' };
  r.status = (c: number) => { r.statusCode = c; return r; };
  r.send = (b: string) => { r.body = b; return r; };
  r.json = (b: unknown) => { r.body = JSON.stringify(b); return r; };
  r.redirect = (u: string) => { r.redirectedTo = u; return r; };
  r.cookie = (n: string, v: string) => { r.cookies[n] = v; return r; };
  r.clearCookie = () => r;
  return r;
}
function hmacFor(query: Record<string, string>) {
  const msg = Object.keys(query).sort().map((k) => `${k}=${query[k]}`).join('&');
  return crypto.createHmac('sha256', 'shpss_secret_abc').update(msg).digest('hex');
}

test('shop validation', () => {
  assert.equal(isValidShop('smori-9216.myshopify.com'), true);
  assert.equal(isValidShop('smori-9216.myshopify.com.attacker.example'), false);
  assert.equal(isValidShop('https://x.myshopify.com'), false);
});

test('token store encrypts at rest and round-trips', async () => {
  await saveSession({ shop: 'smori-9216.myshopify.com', accessToken: 'shpat_supersecret', scope: 'write_files' });
  const raw = await fs.readFile(path.join(tmp, 'sessions.enc.json'), 'utf8');
  assert.ok(!raw.includes('shpat_supersecret'), 'token is not stored in clear text');
  const s = await getSession('smori-9216.myshopify.com');
  assert.equal(s?.accessToken, 'shpat_supersecret');
  assert.deepEqual(await listShops(), ['smori-9216.myshopify.com']);
  const resolved = await resolveSession();
  assert.equal(resolved.accessToken, 'shpat_supersecret');
  assert.equal(await deleteSession('smori-9216.myshopify.com'), true);
  await assert.rejects(resolveSession(), /not installed/);
});

test('authorization code grant: begin -> callback stores the token', async () => {
  const res1 = fakeRes();
  beginAuth({ query: { shop: 'smori-9216.myshopify.com' } } as any, res1);
  const url = new URL(res1.redirectedTo);
  assert.equal(url.origin + url.pathname, 'https://smori-9216.myshopify.com/admin/oauth/authorize');
  assert.equal(url.searchParams.get('client_id'), 'client-id-123');
  assert.equal(url.searchParams.get('redirect_uri'), 'https://app.example.com/auth/callback');
  assert.match(url.searchParams.get('scope')!, /write_metaobject_definitions/);
  const state = url.searchParams.get('state')!;
  const cookie = res1.cookies['smori_oauth_state'];
  assert.ok(cookie.startsWith(`${state}|smori-9216.myshopify.com|`));

  const query = { code: 'abc123', shop: 'smori-9216.myshopify.com', state, timestamp: '1700000000', host: 'aGk=' };
  const fullQuery = { ...query, hmac: hmacFor(query) };
  assert.equal(verifyQueryHmac(fullQuery), true);
  assert.equal(verifyQueryHmac({ ...fullQuery, code: 'tampered' }), false);

  const calls: { url: string; body: any }[] = [];
  const fakeFetch = (async (url: string, init: any) => {
    calls.push({ url, body: JSON.parse(init.body) });
    return new Response(JSON.stringify({ access_token: 'shpat_new_token', scope: 'write_files,write_metaobjects,write_metaobject_definitions' }), { status: 200 });
  }) as unknown as typeof fetch;

  // wrong state cookie is rejected
  const bad = fakeRes();
  await handleCallback({ query: fullQuery, headers: { cookie: 'smori_oauth_state=wrong|smori-9216.myshopify.com|sig' } } as any, bad, fakeFetch);
  assert.equal(bad.statusCode, 400);
  assert.equal(calls.length, 0);

  const res2 = fakeRes();
  await handleCallback({ query: fullQuery, headers: { cookie: `smori_oauth_state=${cookie}` } } as any, res2, fakeFetch);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://smori-9216.myshopify.com/admin/oauth/access_token');
  assert.deepEqual(calls[0].body, { client_id: 'client-id-123', client_secret: 'shpss_secret_abc', code: 'abc123' });
  assert.match(res2.redirectedTo, /^\/\?shop=smori-9216/);
  assert.equal((await getSession('smori-9216.myshopify.com'))?.accessToken, 'shpat_new_token');
  // read_* is satisfied by write_*
  assert.equal(res2.statusCode, 200);
});

test('app/uninstalled webhook deletes the token only with a valid hmac', async () => {
  const body = Buffer.from(JSON.stringify({ id: 1 }));
  const good = crypto.createHmac('sha256', 'shpss_secret_abc').update(body).digest('base64');
  assert.equal(verifyWebhookHmac(body, good), true);
  assert.equal(verifyWebhookHmac(body, 'nope'), false);
  const res = fakeRes();
  await handleWebhook({ body, headers: { 'x-shopify-topic': 'app/uninstalled', 'x-shopify-shop-domain': 'smori-9216.myshopify.com', 'x-shopify-hmac-sha256': 'bad' } } as any, res);
  assert.equal(res.statusCode, 401);
  assert.ok(await getSession('smori-9216.myshopify.com'));
  const res2 = fakeRes();
  await handleWebhook({ body, headers: { 'x-shopify-topic': 'app/uninstalled', 'x-shopify-shop-domain': 'smori-9216.myshopify.com', 'x-shopify-hmac-sha256': good } } as any, res2);
  assert.equal(res2.statusCode, 200);
  assert.equal(await getSession('smori-9216.myshopify.com'), null);
});
