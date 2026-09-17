import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from './config.js';
import { generateCopy } from './copy.js';
import { blocksToRichText } from './richtext.js';
import { createDraftMetaobject, ensureDefinition, uploadImage } from './shopify.js';
import { getProject, photoPath, projectDir, saveProject, type Project } from './store.js';

export type Log = (message: string) => void;

export async function runGenerate(projectId: string, log: Log = console.log): Promise<Project> {
  const project = await getProject(projectId);
  project.copy = await generateCopy(project, log);
  await saveProject(project);
  return project;
}

/** Field values exactly as they will be sent to metaobjectCreate. */
export function buildMetaobjectFields(project: Project, fileIds: { cover?: string; photos: string[] }): Record<string, string> {
  const c = project.copy;
  if (!c) throw new Error('generate the copy first');
  const f = project.facts;
  const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);
  return {
    title: c.title,
    title_zh: c.title_zh,
    subtitle: c.subtitle,
    subtitle_zh: c.subtitle_zh,
    category: f.category,
    product_name: f.product_name,
    room: f.room || c.room_observed || '',
    room_zh: f.room_zh || c.room_observed_zh || '',
    location: f.location,
    installed_on: f.installed_on || '',
    summary: c.summary,
    summary_zh: c.summary_zh,
    description: JSON.stringify(blocksToRichText(c.description)),
    description_zh: JSON.stringify(blocksToRichText(c.description_zh)),
    cover: fileIds.cover ?? '',
    photos: fileIds.photos.length ? JSON.stringify(fileIds.photos) : '',
    featured: 'false',
    xhs_title: c.xhs_title,
    xhs_body: c.xhs_body,
    _handle: slug(`${c.title}-${project.id}`),
  };
}

export interface DraftOptions { dryRun?: boolean }

/** Uploads the web-sized photos to Shopify Files (skipping ones already uploaded) and creates the DRAFT metaobject. */
export async function runSaveDraft(projectId: string, opts: DraftOptions = {}, log: Log = console.log): Promise<Project> {
  const project = await getProject(projectId);
  if (!project.copy) throw new Error('generate the copy first');
  if (!project.photos.length) throw new Error('no photos');
  if (project.shopify && !project.shopify.dryRun) throw new Error(`already saved as ${project.shopify.metaobjectId}`);

  if (opts.dryRun) {
    const fields = buildMetaobjectFields(project, { cover: 'gid://shopify/MediaImage/DRY-RUN-COVER', photos: project.photos.map((p, i) => `gid://shopify/MediaImage/DRY-RUN-${i + 1}`) });
    const handle = fields._handle;
    delete fields._handle;
    const out = path.join(projectDir(project.id), 'shopify-payload.dry-run.json');
    await fs.writeFile(out, JSON.stringify({ type: config.metaobjectType, handle, capabilities: { publishable: { status: 'DRAFT' } }, fields }, null, 2));
    project.shopify = { metaobjectId: 'dry-run', handle, status: 'DRAFT', adminUrl: '', createdAt: new Date().toISOString(), dryRun: true };
    await saveProject(project);
    log(`dry run: payload written to ${out}`);
    return project;
  }

  const def = await ensureDefinition();
  log(`metaobject definition ${config.metaobjectType}: ${def.action}${def.addedFields.length ? ` (added ${def.addedFields.join(', ')})` : ''}`);

  const ordered = [...project.photos].sort((a, b) => a.order - b.order);
  for (const photo of ordered) {
    if (photo.shopifyFileId) { log(`skip ${photo.id} (already ${photo.shopifyFileId})`); continue; }
    const alt = photo.caption || `${project.facts.product_name} installation, ${project.facts.location}`;
    const filename = `smori-${project.id}-${photo.id}.jpg`;
    const up = await uploadImage(photoPath(project.id, 'web', photo.id + '.jpg'), filename, alt, log);
    photo.shopifyFileId = up.id;
    photo.shopifyUrl = up.url;
    await saveProject(project); // persist progress so a retry does not re-upload
  }

  const cover = ordered.find((p) => p.cover) ?? ordered[0];
  const fields = buildMetaobjectFields(project, { cover: cover.shopifyFileId, photos: ordered.map((p) => p.shopifyFileId!) });
  const handle = fields._handle;
  delete fields._handle;
  const created = await createDraftMetaobject(fields, handle);
  project.shopify = { metaobjectId: created.id, handle: created.handle, status: 'DRAFT', adminUrl: created.adminUrl, createdAt: new Date().toISOString() };
  await saveProject(project);
  log(`draft created: ${created.id} (${created.handle})`);
  return project;
}
