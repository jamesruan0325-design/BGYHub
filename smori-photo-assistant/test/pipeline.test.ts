import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import sharp from 'sharp';

// Point the app at a temp data dir and the fake endpoint BEFORE importing app modules.
const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'smori-test-'));
process.env.DATA_DIR = tmp;
process.env.SHOPIFY_ADMIN_TOKEN = 'shpat_fake';
const { startFakeShopify } = await import('./fake-shopify.js');
const fake = await startFakeShopify();
process.env.SHOPIFY_ADMIN_ENDPOINT = fake.endpoint;

const { processUpload } = await import('../src/images.js');
const { createProject, getProject, saveProject } = await import('../src/store.js');
const { blocksToRichText, blocksToText, textToBlocks } = await import('../src/richtext.js');
const { ensureDefinition, shopInfo } = await import('../src/shopify.js');
const { runSaveDraft, buildMetaobjectFields } = await import('../src/pipeline.js');
const { FIELD_DEFINITIONS } = await import('../src/shopify.js');

after(async () => { await fake.close(); await fs.rm(tmp, { recursive: true, force: true }); });

async function testImage(w: number, h: number, color: string, withExif = false): Promise<Buffer> {
  let img = sharp({ create: { width: w, height: h, channels: 3, background: color } }).jpeg({ quality: 95 });
  if (withExif) img = img.withExif({ IFD0: { Orientation: '6', ImageDescription: 'gps-like private data' } });
  return img.toBuffer();
}

test('rich text conversion and editable text round trip', () => {
  const blocks = [
    { type: 'heading' as const, text: 'The brief' },
    { type: 'paragraph' as const, text: 'Privacy without losing daylight.' },
    { type: 'list' as const, items: ['4 windows measured', 'Installed in one visit'] },
  ];
  const rt = blocksToRichText(blocks);
  assert.equal(rt.type, 'root');
  assert.deepEqual(rt.children![0], { type: 'heading', level: 2, children: [{ type: 'text', value: 'The brief' }] });
  assert.equal(rt.children![2].listType, 'unordered');
  assert.equal(rt.children![2].children![1].children![0].value, 'Installed in one visit');
  const text = blocksToText(blocks);
  assert.deepEqual(textToBlocks(text), blocks);
  assert.deepEqual(textToBlocks('## A\n\nline one\nline two\n\n- x\n- y'), [
    { type: 'heading', text: 'A' }, { type: 'paragraph', text: 'line one line two' }, { type: 'list', items: ['x', 'y'] },
  ]);
});

test('image pipeline keeps the original, resizes and strips metadata', async () => {
  const project = await createProject({ product_name: 'Silhouette', category: 'sheer', location: 'Irvine, CA' });
  const big = await testImage(4000, 3000, '#b8977e', true);
  const { photo, webPath } = await processUpload(project.id, big, 'IMG_0001.JPG', 0);
  const original = await fs.readFile(path.join(tmp, 'projects', project.id, 'original', photo.id + '.jpg'));
  assert.equal(original.byteLength, big.byteLength, 'original stored byte-for-byte');
  const meta = await sharp(webPath).metadata();
  assert.equal(Math.max(meta.width!, meta.height!), 2000, 'long edge limited to 2000px');
  assert.equal(meta.exif, undefined, 'EXIF (incl. GPS) removed from the web copy');
  assert.ok(photo.webBytes < photo.originalBytes);
  assert.equal(photo.cover, true);
  await assert.rejects(processUpload(project.id, Buffer.from('not an image'), 'x.txt', 1), /Unsupported|Input buffer|unsupported/i);
});

test('end-to-end against the fake Shopify: definition, uploads, DRAFT metaobject', async () => {
  const info = await shopInfo();
  assert.equal(info.name, 'Fake SMORI');

  const project = await createProject({ product_name: 'Duette', category: 'blackout', location: 'Newport Beach, CA', room: 'Master Bedroom', installed_on: '2026-09-01' });
  for (const [i, c] of ['#3d3028', '#2a3a4a', '#4a3a2a'].entries()) {
    const { photo } = await processUpload(project.id, await testImage(2400, 1600, c), `photo-${i}.jpg`, i);
    project.photos.push(photo);
  }
  project.photos[1].cover = true; project.photos[0].cover = false;
  project.copy = {
    title: 'Master Suite Retreat', title_zh: '主卧静谧空间', subtitle: 'Duette honeycomb shades in a master bedroom', subtitle_zh: '主卧的 Duette 蜂巢帘',
    summary: 'Room-darkening Duette shades for a west-facing bedroom.', summary_zh: '为朝西主卧安装遮光 Duette 蜂巢帘。',
    description: [{ type: 'heading', text: 'The brief' }, { type: 'paragraph', text: 'Block the afternoon sun.' }],
    description_zh: [{ type: 'heading', text: '项目需求' }, { type: 'paragraph', text: '遮挡午后西晒。' }],
    xhs_title: '主卧遮光这样做', xhs_body: '……#窗帘 #尔湾', warnings: [], generatedAt: new Date().toISOString(),
  };
  await saveProject(project);

  // no definition yet -> created by ensureDefinition inside runSaveDraft
  assert.equal(fake.state.definition, null);
  const logs: string[] = [];
  const done = await runSaveDraft(project.id, {}, (m) => logs.push(m));

  assert.equal(fake.state.definition!.type, 'installation_case');
  assert.equal(fake.state.definition!.fields.length, FIELD_DEFINITIONS.length);
  assert.equal(fake.state.definition!.publishable, true);
  assert.equal(fake.state.uploads.length, 3, 'three staged uploads');
  assert.equal(fake.state.files.size, 3);
  assert.equal(fake.state.metaobjects.length, 1);
  const mo = fake.state.metaobjects[0];
  assert.equal(mo.status, 'DRAFT');
  assert.equal(mo.fields.category, 'blackout');
  assert.equal(mo.fields.product_name, 'Duette');
  assert.equal(mo.fields.location, 'Newport Beach, CA');
  assert.equal(mo.fields.installed_on, '2026-09-01');
  assert.equal(mo.fields.featured, 'false');
  const photoIds = JSON.parse(mo.fields.photos) as string[];
  assert.equal(photoIds.length, 3);
  assert.equal(mo.fields.cover, done.photos[1].shopifyFileId, 'cover is the photo marked as cover');
  assert.equal(JSON.parse(mo.fields.description).children[0].type, 'heading');
  assert.ok(mo.handle.startsWith('master-suite-retreat-'));
  assert.equal(done.shopify!.metaobjectId, mo.id);
  assert.match(done.shopify!.adminUrl, /admin\.shopify\.com\/store\/.+\/content\/entries\/installation_case\/\d+$/);

  // re-run is refused (no duplicate drafts); photos keep their file ids
  await assert.rejects(runSaveDraft(project.id), /already saved/);
  const reread = await getProject(project.id);
  assert.ok(reread.photos.every((p) => p.shopifyFileId));

  // second project reuses the now-existing definition (no create call)
  const before = fake.state.requests.filter((r) => r === 'metaobjectDefinitionCreate').length;
  const r = await ensureDefinition();
  assert.equal(r.action, 'ok');
  assert.equal(fake.state.requests.filter((x) => x === 'metaobjectDefinitionCreate').length, before);

  // dry run writes the payload file without touching Shopify
  const p2 = await createProject({ product_name: 'Silhouette', category: 'sheer', location: 'Irvine, CA' });
  const { photo } = await processUpload(p2.id, await testImage(1200, 900, '#333'), 'a.jpg', 0);
  p2.photos.push(photo); p2.copy = project.copy; await saveProject(p2);
  const metaobjectsBefore = fake.state.metaobjects.length;
  const dry = await runSaveDraft(p2.id, { dryRun: true }, () => {});
  assert.equal(dry.shopify!.dryRun, true);
  assert.equal(fake.state.metaobjects.length, metaobjectsBefore);
  const payload = JSON.parse(await fs.readFile(path.join(tmp, 'projects', p2.id, 'shopify-payload.dry-run.json'), 'utf8'));
  assert.equal(payload.capabilities.publishable.status, 'DRAFT');
  const fields = buildMetaobjectFields(p2, { cover: 'x', photos: ['x'] });
  assert.equal(fields.room, '', 'room stays empty when neither staff nor model provided one');
});
