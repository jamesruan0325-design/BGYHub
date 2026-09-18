import express from 'express';
import multer from 'multer';
import path from 'node:path';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { config, CATEGORIES, PRODUCTS } from './config.js';
import { processUpload } from './images.js';
import { blocksToText, textToBlocks } from './richtext.js';
import { getDefinition, shopInfo, shopifyConfigured, ensureDefinition, resolveSession } from './shopify.js';
import { beginAuth, handleCallback, isValidShop, oauthConfigured, verifyQueryHmac } from './oauth.js';
import { handleWebhook } from './webhooks.js';
import { getSession } from './tokens.js';
import { claudeConfigured } from './copy.js';
import { runGenerate, runSaveDraft, buildMetaobjectFields } from './pipeline.js';
import { createProject, deletePhotoFiles, getProject, listProjects, photoPath, saveProject, type ProjectFacts } from './store.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const param = (req: express.Request, name: string) => String(req.params[name] ?? '');
const app = express();
app.set('trust proxy', 1); // behind Fly/Render/Cloudflare: correct req.secure and cookies

// ---- Shopify OAuth + webhooks (no basic auth; Shopify calls these) ----
app.get('/auth', (req, res) => beginAuth(req, res));
app.get('/auth/callback', wrapPlain((req, res) => handleCallback(req, res)));
app.post('/webhooks/:topic', express.raw({ type: '*/*', limit: '1mb' }), wrapPlain(handleWebhook));
app.get('/healthz', (_req, res) => { res.json({ ok: true }); });

app.use(express.json({ limit: '2mb' }));

// ---- entry from Shopify admin (non-embedded app opens in its own tab with ?shop&hmac&host&timestamp) ----
app.get('/', (req, res, next) => {
  const shop = req.query.shop;
  if (!isValidShop(shop)) return next();
  if (req.query.hmac && !verifyQueryHmac(req.query as Record<string, unknown>)) { res.status(400).send('invalid hmac'); return; }
  getSession(shop).then((s) => {
    if (!s && oauthConfigured()) return res.redirect(`/auth?shop=${encodeURIComponent(shop)}`);
    next();
  }).catch(next);
});

// ---- basic auth (user "smori") for the staff UI and API ----
app.use((req, res, next) => {
  if (!config.adminPassword) {
    if (config.nodeEnv === 'production') { res.status(503).send('ADMIN_PASSWORD is not set'); return; }
    return next();
  }
  const h = req.headers.authorization ?? '';
  const ok = h.startsWith('Basic ') && Buffer.from(h.slice(6), 'base64').toString() === `smori:${config.adminPassword}`;
  if (ok) return next();
  res.set('WWW-Authenticate', 'Basic realm="SMORI photo assistant"').status(401).send('auth required');
});

app.use(express.static(path.join(here, '..', 'public')));

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 40 * 1024 * 1024, files: 30 } });

type Handler = (req: express.Request, res: express.Response) => Promise<void>;
const wrap = (fn: Handler) => (req: express.Request, res: express.Response) => fn(req, res).catch((err: Error) => {
  console.error(err);
  res.status(400).json({ error: err.message });
});
function wrapPlain(fn: Handler) {
  return (req: express.Request, res: express.Response) => fn(req, res).catch((err: Error) => { console.error(err); res.status(500).send(err.message); });
}

function cleanFacts(input: Partial<ProjectFacts>, existing?: ProjectFacts): ProjectFacts {
  const s = (v: unknown) => (typeof v === 'string' ? v.trim() : '');
  const facts: ProjectFacts = {
    product_name: s(input.product_name) || existing?.product_name || '',
    category: s(input.category) || existing?.category || '',
    location: s(input.location) || existing?.location || '',
    room: s(input.room) || (input.room === '' ? '' : existing?.room),
    room_zh: s(input.room_zh) || (input.room_zh === '' ? '' : existing?.room_zh),
    installed_on: s(input.installed_on) || (input.installed_on === '' ? '' : existing?.installed_on),
    title: s(input.title) || (input.title === '' ? '' : existing?.title),
    notes: s(input.notes) || (input.notes === '' ? '' : existing?.notes),
  };
  if (!facts.product_name) throw new Error('product_name is required (confirmed by staff)');
  if (!facts.location) throw new Error('location is required (confirmed by staff)');
  if (!CATEGORIES.some((c) => c.value === facts.category)) throw new Error('category must be one of ' + CATEGORIES.map((c) => c.value).join(', '));
  if (facts.installed_on && !/^\d{4}-\d{2}-\d{2}$/.test(facts.installed_on)) throw new Error('installed_on must be YYYY-MM-DD');
  return facts;
}

// ---- status ----
app.get('/api/health', wrap(async (_req, res) => {
  const installed = await shopifyConfigured();
  const out: Record<string, unknown> = {
    claude: { configured: claudeConfigured(), model: config.claudeModel },
    shopify: { configured: installed, shop: config.shop, apiVersion: config.apiVersion, oauth: oauthConfigured(), installUrl: oauthConfigured() ? `${config.appUrl}/auth?shop=${config.shop}` : null },
    categories: CATEGORIES, products: PRODUCTS.map((p) => ({ name: p.name, subtitle: p.subtitle, category: p.category })),
  };
  if (installed) {
    try {
      const session = await resolveSession();
      const info = await shopInfo();
      (out.shopify as Record<string, unknown>).session = { shop: session.shop, scope: session.scope, installedAt: session.installedAt };
      const def = await getDefinition();
      out.shopify = { ...(out.shopify as object), connected: true, name: info.name, definition: def ? { exists: true, publishable: def.publishable, onlineStore: def.onlineStore, fields: def.fieldKeys.length } : { exists: false } };
    } catch (e) {
      out.shopify = { ...(out.shopify as object), connected: false, error: (e as Error).message };
    }
  }
  res.json(out);
}));

app.post('/api/setup', wrap(async (_req, res) => {
  res.json(await ensureDefinition());
}));

// ---- projects ----
app.get('/api/projects', wrap(async (_req, res) => {
  const list = await listProjects();
  res.json(list.map((p) => ({ id: p.id, createdAt: p.createdAt, facts: p.facts, photos: p.photos.length, hasCopy: Boolean(p.copy), shopify: p.shopify ?? null, cover: p.photos.find((x) => x.cover)?.id ?? p.photos[0]?.id ?? null })));
}));

app.post('/api/projects', wrap(async (req, res) => {
  const project = await createProject(cleanFacts(req.body));
  res.json(project);
}));

app.get('/api/projects/:id', wrap(async (req, res) => {
  const p = await getProject(param(req, 'id'));
  res.json({ ...p, copyText: p.copy ? { description: blocksToText(p.copy.description), description_zh: blocksToText(p.copy.description_zh) } : null });
}));

app.patch('/api/projects/:id', wrap(async (req, res) => {
  const p = await getProject(param(req, 'id'));
  const body = req.body ?? {};
  if (body.facts) p.facts = cleanFacts(body.facts, p.facts);
  if (body.copy && p.copy) {
    const c = body.copy;
    for (const k of ['title', 'title_zh', 'subtitle', 'subtitle_zh', 'summary', 'summary_zh', 'xhs_title', 'xhs_body'] as const) {
      if (typeof c[k] === 'string') p.copy[k] = c[k].trim();
    }
    if (typeof c.description_text === 'string') p.copy.description = textToBlocks(c.description_text);
    if (typeof c.description_zh_text === 'string') p.copy.description_zh = textToBlocks(c.description_zh_text);
  }
  if (Array.isArray(body.photoOrder)) {
    body.photoOrder.forEach((id: string, i: number) => { const ph = p.photos.find((x) => x.id === id); if (ph) ph.order = i; });
    p.photos.sort((a, b) => a.order - b.order);
  }
  if (typeof body.coverId === 'string') p.photos.forEach((ph) => { ph.cover = ph.id === body.coverId; });
  if (body.captions && typeof body.captions === 'object') {
    for (const [id, cap] of Object.entries(body.captions as Record<string, { caption?: string; caption_zh?: string }>)) {
      const ph = p.photos.find((x) => x.id === id);
      if (ph) { if (typeof cap.caption === 'string') ph.caption = cap.caption; if (typeof cap.caption_zh === 'string') ph.caption_zh = cap.caption_zh; }
    }
  }
  await saveProject(p);
  res.json(p);
}));

app.post('/api/projects/:id/photos', upload.array('photos', 30), wrap(async (req, res) => {
  const p = await getProject(param(req, 'id'));
  const files = (req.files as Express.Multer.File[]) ?? [];
  if (!files.length) throw new Error('no files received (field name must be "photos")');
  const errors: string[] = [];
  for (const f of files) {
    try {
      const { photo } = await processUpload(p.id, f.buffer, f.originalname, p.photos.length);
      p.photos.push(photo);
    } catch (e) { errors.push((e as Error).message); }
  }
  if (!p.photos.some((x) => x.cover) && p.photos[0]) p.photos[0].cover = true;
  await saveProject(p);
  res.json({ project: p, errors });
}));

app.delete('/api/projects/:id/photos/:photoId', wrap(async (req, res) => {
  const p = await getProject(param(req, 'id'));
  const ph = p.photos.find((x) => x.id === param(req, 'photoId'));
  if (!ph) throw new Error('photo not found');
  if (ph.shopifyFileId) throw new Error('photo already uploaded to Shopify; remove it there');
  p.photos = p.photos.filter((x) => x.id !== ph.id);
  p.photos.forEach((x, i) => { x.order = i; });
  if (!p.photos.some((x) => x.cover) && p.photos[0]) p.photos[0].cover = true;
  await saveProject(p);
  const original = (await fs.readdir(path.join(photoPath(p.id, 'original', ''), '..', 'original'))).find((n) => n.startsWith(ph.id));
  await deletePhotoFiles(p.id, ph.id + '.jpg');
  if (original) await fs.rm(photoPath(p.id, 'original', original), { force: true });
  res.json(p);
}));

app.post('/api/projects/:id/generate', wrap(async (req, res) => {
  const logs: string[] = [];
  const p = await runGenerate(param(req, 'id'), (m) => logs.push(m));
  res.json({ project: p, logs, copyText: { description: blocksToText(p.copy!.description), description_zh: blocksToText(p.copy!.description_zh) } });
}));

app.get('/api/projects/:id/preview-payload', wrap(async (req, res) => {
  const p = await getProject(param(req, 'id'));
  res.json(buildMetaobjectFields(p, { cover: '(cover file id)', photos: p.photos.map(() => '(file id)') }));
}));

app.post('/api/projects/:id/shopify-draft', wrap(async (req, res) => {
  const logs: string[] = [];
  const p = await runSaveDraft(param(req, 'id'), { dryRun: Boolean(req.body?.dryRun) }, (m) => logs.push(m));
  res.json({ project: p, logs });
}));

// ---- files ----
app.get('/files/:id/:kind/:name', wrap(async (req, res) => {
  const kind = param(req, 'kind') as 'original' | 'web' | 'thumb';
  if (!['original', 'web', 'thumb'].includes(kind)) throw new Error('bad kind');
  const name = path.basename(param(req, 'name'));
  res.sendFile(photoPath(param(req, 'id'), kind, name));
}));

app.listen(config.port, () => {
  console.log(`SMORI photo assistant: http://localhost:${config.port}  (data: ${config.dataDir})`);
  console.log(`Claude: ${claudeConfigured() ? config.claudeModel : 'NOT configured'} | OAuth: ${oauthConfigured() ? `${config.appUrl} (client ${config.apiKey.slice(0, 6)}…)` : 'NOT configured'}`);
  shopifyConfigured().then((ok) => console.log(`Shopify: ${ok ? `installed on ${config.shop}` : `not installed on ${config.shop} yet`}`));
});
