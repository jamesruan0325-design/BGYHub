/**
 * Headless pipeline, mainly for the end-to-end test.
 *
 *   npm run cli -- check
 *   npm run cli -- setup
 *   npm run cli -- e2e --product Silhouette --category sheer --location "Irvine, CA" \
 *        --room "Living Room" --date 2026-09-01 --notes "..." --photos ./photos/*.jpg [--dry-run] [--skip-claude]
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from './config.js';
import { processUpload } from './images.js';
import { ensureDefinition, getDefinition, shopInfo, shopifyConfigured } from './shopify.js';
import { claudeConfigured } from './copy.js';
import { runGenerate, runSaveDraft } from './pipeline.js';
import { createProject, saveProject, type GeneratedCopy } from './store.js';

function parseArgs(argv: string[]) {
  const args: Record<string, string | boolean | string[]> = {};
  let key: string | null = null;
  for (const a of argv) {
    if (a.startsWith('--')) { key = a.slice(2); args[key] = true; }
    else if (key) {
      const cur = args[key];
      if (cur === true) args[key] = a;
      else if (Array.isArray(cur)) cur.push(a);
      else args[key] = [cur as string, a];
    }
  }
  return args;
}

async function main() {
  const [cmd, ...rest] = process.argv.slice(2);
  const args = parseArgs(rest);
  const log = (m: string) => console.log(`  ${m}`);

  if (cmd === 'check') {
    console.log(`Claude:  ${claudeConfigured() ? `configured (${config.claudeModel})` : 'ANTHROPIC_API_KEY missing'}`);
    if (!shopifyConfigured()) { console.log('Shopify: SHOPIFY_ADMIN_TOKEN missing'); return; }
    const info = await shopInfo();
    console.log(`Shopify: connected to "${info.name}" (${info.myshopifyDomain}), API ${config.apiVersion}`);
    const def = await getDefinition();
    console.log(def ? `Definition ${config.metaobjectType}: ${def.fieldKeys.length} fields, publishable=${def.publishable}, onlineStore=${def.onlineStore}` : `Definition ${config.metaobjectType}: NOT created yet (run: npm run cli -- setup)`);
    return;
  }

  if (cmd === 'setup') {
    const r = await ensureDefinition();
    console.log(`definition ${r.action}; fields: ${r.definition.fieldKeys.join(', ')}`);
    return;
  }

  if (cmd === 'e2e') {
    const photos = ([] as string[]).concat(args.photos as string[] ?? []);
    if (!photos.length) throw new Error('--photos <files…> required');
    for (const k of ['product', 'category', 'location']) if (typeof args[k] !== 'string') throw new Error(`--${k} required (confirmed by staff)`);
    const project = await createProject({
      product_name: args.product as string, category: args.category as string, location: args.location as string,
      room: typeof args.room === 'string' ? args.room : undefined, room_zh: typeof args['room-zh'] === 'string' ? args['room-zh'] : undefined,
      installed_on: typeof args.date === 'string' ? args.date : undefined, notes: typeof args.notes === 'string' ? args.notes : undefined,
    });
    console.log(`1. project ${project.id}`);
    for (const [i, file] of photos.entries()) {
      const buf = await fs.readFile(file);
      const { photo } = await processUpload(project.id, buf, path.basename(file), i);
      project.photos.push(photo);
      log(`${path.basename(file)}: ${Math.round(photo.originalBytes / 1024)} KB -> web ${Math.round(photo.webBytes / 1024)} KB (${photo.width}x${photo.height})`);
    }
    await saveProject(project);

    if (args['skip-claude']) {
      console.log('2. copy: skipped (--skip-claude), using placeholder copy');
      const placeholder: GeneratedCopy = {
        title: `${project.facts.product_name} in ${project.facts.location}`, title_zh: `${project.facts.location} ${project.facts.product_name} 安装案例`,
        subtitle: `${project.facts.product_name} installation`, subtitle_zh: `${project.facts.product_name} 安装`,
        summary: 'Placeholder summary (copy generation skipped).', summary_zh: '占位简介（未生成文案）。',
        description: [{ type: 'paragraph', text: 'Placeholder.' }], description_zh: [{ type: 'paragraph', text: '占位。' }],
        xhs_title: '占位', xhs_body: '占位', warnings: ['copy generation was skipped'], generatedAt: new Date().toISOString(),
      };
      project.copy = placeholder;
      await saveProject(project);
    } else {
      console.log('2. generating copy');
      const p = await runGenerate(project.id, log);
      console.log(`   EN: ${p.copy!.title} — ${p.copy!.summary}`);
      console.log(`   ZH: ${p.copy!.title_zh} — ${p.copy!.summary_zh}`);
      if (p.copy!.warnings.length) console.log(`   warnings: ${p.copy!.warnings.join(' | ')}`);
    }

    console.log(args['dry-run'] ? '3. Shopify: dry run' : '3. Shopify: uploading photos and creating DRAFT metaobject');
    const done = await runSaveDraft(project.id, { dryRun: Boolean(args['dry-run']) }, log);
    console.log(`   result: ${JSON.stringify(done.shopify)}`);
    return;
  }

  console.log('commands: check | setup | e2e');
}

main().catch((e) => { console.error(`ERROR: ${e.message}`); process.exit(1); });
