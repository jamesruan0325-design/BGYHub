import fs from 'node:fs/promises';
import { config } from './config.js';

export class ShopifyError extends Error {}

function endpoint(): string {
  if (config.adminEndpoint) return config.adminEndpoint;
  return `https://${config.shop}/admin/api/${config.apiVersion}/graphql.json`;
}

export function shopifyConfigured(): boolean {
  return Boolean(config.adminToken || config.adminEndpoint);
}

export async function graphql<T = any>(query: string, variables: Record<string, unknown> = {}): Promise<T> {
  if (!shopifyConfigured()) throw new ShopifyError('SHOPIFY_ADMIN_TOKEN is not set');
  const res = await fetch(endpoint(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Shopify-Access-Token': config.adminToken },
    body: JSON.stringify({ query, variables }),
  });
  if (!res.ok) throw new ShopifyError(`Shopify HTTP ${res.status}: ${(await res.text()).slice(0, 500)}`);
  const json = (await res.json()) as { data?: T; errors?: { message: string }[] };
  if (json.errors?.length) throw new ShopifyError(`Shopify GraphQL: ${json.errors.map((e) => e.message).join('; ')}`);
  if (!json.data) throw new ShopifyError('Shopify GraphQL: empty response');
  return json.data;
}

function assertNoUserErrors(where: string, errors: { field?: string[] | null; message: string; code?: string }[] | undefined) {
  if (errors?.length) {
    throw new ShopifyError(`${where}: ${errors.map((e) => `${(e.field ?? []).join('.') || '-'}: ${e.message}${e.code ? ` (${e.code})` : ''}`).join('; ')}`);
  }
}

/* ------------------------------------------------------------------ */
/* Connection test                                                     */
/* ------------------------------------------------------------------ */

export async function shopInfo(): Promise<{ name: string; myshopifyDomain: string }> {
  const d = await graphql<{ shop: { name: string; myshopifyDomain: string } }>(`{ shop { name myshopifyDomain } }`);
  return d.shop;
}

/* ------------------------------------------------------------------ */
/* Metaobject definition (mirrors docs/smori/02-...md)                 */
/* ------------------------------------------------------------------ */

const CHOICES = ['sheer', 'blackout', 'signature', 'motorized', 'drapery', 'shutters', 'commercial'];

export const FIELD_DEFINITIONS = [
  { key: 'title', name: 'Title (EN)', type: 'single_line_text_field', required: true },
  { key: 'title_zh', name: '标题（中文）', type: 'single_line_text_field' },
  { key: 'subtitle', name: 'Subtitle (EN)', type: 'single_line_text_field' },
  { key: 'subtitle_zh', name: '副标题（中文）', type: 'single_line_text_field' },
  { key: 'category', name: 'Category', type: 'single_line_text_field', required: true, validations: [{ name: 'choices', value: JSON.stringify(CHOICES) }] },
  { key: 'product_name', name: 'Product', type: 'single_line_text_field' },
  { key: 'product', name: 'Product (Shopify)', type: 'product_reference' },
  { key: 'room', name: 'Room (EN)', type: 'single_line_text_field' },
  { key: 'room_zh', name: '空间（中文）', type: 'single_line_text_field' },
  { key: 'location', name: 'Location', type: 'single_line_text_field' },
  { key: 'installed_on', name: 'Installed on', type: 'date' },
  { key: 'summary', name: 'Summary (EN)', type: 'multi_line_text_field' },
  { key: 'summary_zh', name: '简介（中文）', type: 'multi_line_text_field' },
  { key: 'description', name: 'Description (EN)', type: 'rich_text_field' },
  { key: 'description_zh', name: '项目介绍（中文）', type: 'rich_text_field' },
  { key: 'cover', name: 'Cover photo', type: 'file_reference', validations: [{ name: 'file_type_options', value: JSON.stringify(['Image']) }] },
  { key: 'photos', name: 'Photos', type: 'list.file_reference', validations: [{ name: 'file_type_options', value: JSON.stringify(['Image']) }] },
  { key: 'featured', name: 'Featured', type: 'boolean' },
  { key: 'xhs_title', name: '小红书标题', type: 'single_line_text_field' },
  { key: 'xhs_body', name: '小红书正文', type: 'multi_line_text_field' },
];

export interface DefinitionInfo {
  id: string;
  type: string;
  fieldKeys: string[];
  publishable: boolean;
  onlineStore: boolean;
}

export async function getDefinition(type = config.metaobjectType): Promise<DefinitionInfo | null> {
  const d = await graphql<{ metaobjectDefinitionByType: null | { id: string; type: string; fieldDefinitions: { key: string; type: { name: string } }[]; capabilities: { publishable: { enabled: boolean }; onlineStore: { enabled: boolean } } } }>(
    `query($type: String!) { metaobjectDefinitionByType(type: $type) { id type fieldDefinitions { key type { name } } capabilities { publishable { enabled } onlineStore { enabled } } } }`,
    { type },
  );
  const def = d.metaobjectDefinitionByType;
  if (!def) return null;
  return { id: def.id, type: def.type, fieldKeys: def.fieldDefinitions.map((f) => f.key), publishable: def.capabilities.publishable.enabled, onlineStore: def.capabilities.onlineStore.enabled };
}

/** Creates the installation_case definition if missing, or adds any missing fields to an existing one. */
export async function ensureDefinition(type = config.metaobjectType): Promise<{ action: 'created' | 'updated' | 'ok'; definition: DefinitionInfo; addedFields: string[] }> {
  const existing = await getDefinition(type);
  if (!existing) {
    const d = await graphql<{ metaobjectDefinitionCreate: { metaobjectDefinition: { id: string } | null; userErrors: any[] } }>(
      `mutation($definition: MetaobjectDefinitionCreateInput!) { metaobjectDefinitionCreate(definition: $definition) { metaobjectDefinition { id } userErrors { field message code } } }`,
      {
        definition: {
          name: 'Installation Case',
          type,
          displayNameField: 'title',
          fieldDefinitions: FIELD_DEFINITIONS,
          capabilities: {
            publishable: { enabled: true },
            onlineStore: { enabled: true, data: { urlHandle: 'installations', createRedirects: false } },
            renderable: { enabled: true, data: { metaTitleKey: 'title', metaDescriptionKey: 'summary' } },
          },
          access: { storefront: 'PUBLIC_READ' },
        },
      },
    );
    assertNoUserErrors('metaobjectDefinitionCreate', d.metaobjectDefinitionCreate.userErrors);
    const created = await getDefinition(type);
    if (!created) throw new ShopifyError('definition created but cannot be read back');
    return { action: 'created', definition: created, addedFields: FIELD_DEFINITIONS.map((f) => f.key) };
  }
  const missing = FIELD_DEFINITIONS.filter((f) => !existing.fieldKeys.includes(f.key));
  if (!missing.length) return { action: 'ok', definition: existing, addedFields: [] };
  const d = await graphql<{ metaobjectDefinitionUpdate: { userErrors: any[] } }>(
    `mutation($id: ID!, $definition: MetaobjectDefinitionUpdateInput!) { metaobjectDefinitionUpdate(id: $id, definition: $definition) { metaobjectDefinition { id } userErrors { field message code } } }`,
    { id: existing.id, definition: { fieldDefinitions: missing.map((f) => ({ create: f })) } },
  );
  assertNoUserErrors('metaobjectDefinitionUpdate', d.metaobjectDefinitionUpdate.userErrors);
  const updated = await getDefinition(type);
  return { action: 'updated', definition: updated!, addedFields: missing.map((f) => f.key) };
}

/* ------------------------------------------------------------------ */
/* Files: staged upload -> fileCreate -> wait until READY              */
/* ------------------------------------------------------------------ */

export interface UploadedFile { id: string; url?: string }

export async function uploadImage(filePath: string, filename: string, alt: string, onLog: (m: string) => void = () => {}): Promise<UploadedFile> {
  const bytes = await fs.readFile(filePath);
  const staged = await graphql<{ stagedUploadsCreate: { stagedTargets: { url: string; resourceUrl: string; parameters: { name: string; value: string }[] }[]; userErrors: any[] } }>(
    `mutation($input: [StagedUploadInput!]!) { stagedUploadsCreate(input: $input) { stagedTargets { url resourceUrl parameters { name value } } userErrors { field message } } }`,
    { input: [{ filename, mimeType: 'image/jpeg', resource: 'IMAGE', httpMethod: 'POST', fileSize: String(bytes.byteLength) }] },
  );
  assertNoUserErrors('stagedUploadsCreate', staged.stagedUploadsCreate.userErrors);
  const target = staged.stagedUploadsCreate.stagedTargets[0];
  if (!target) throw new ShopifyError('stagedUploadsCreate returned no target');

  const form = new FormData();
  for (const p of target.parameters) form.append(p.name, p.value);
  form.append('file', new Blob([bytes], { type: 'image/jpeg' }), filename);
  const up = await fetch(target.url, { method: 'POST', body: form });
  if (!up.ok) throw new ShopifyError(`staged upload failed: HTTP ${up.status} ${(await up.text()).slice(0, 300)}`);
  onLog(`uploaded ${filename} (${Math.round(bytes.byteLength / 1024)} KB)`);

  const created = await graphql<{ fileCreate: { files: { id: string; fileStatus: string }[]; userErrors: any[] } }>(
    `mutation($files: [FileCreateInput!]!) { fileCreate(files: $files) { files { id fileStatus } userErrors { field message code } } }`,
    { files: [{ originalSource: target.resourceUrl, contentType: 'IMAGE', alt, filename, duplicateResolutionMode: 'APPEND_UUID' }] },
  );
  assertNoUserErrors('fileCreate', created.fileCreate.userErrors);
  const file = created.fileCreate.files[0];
  if (!file) throw new ShopifyError('fileCreate returned no file');

  // Shopify processes images asynchronously; a metaobject can reference the file only once it is READY.
  const deadline = Date.now() + 90_000;
  let status = file.fileStatus;
  let url: string | undefined;
  while (status !== 'READY' && status !== 'FAILED') {
    if (Date.now() > deadline) throw new ShopifyError(`file ${file.id} still ${status} after 90s`);
    await new Promise((r) => setTimeout(r, 1500));
    const d = await graphql<{ node: { fileStatus: string; image?: { url: string } } | null }>(
      `query($id: ID!) { node(id: $id) { ... on MediaImage { fileStatus image { url } } } }`,
      { id: file.id },
    );
    status = d.node?.fileStatus ?? 'FAILED';
    url = d.node?.image?.url;
  }
  if (status === 'FAILED') throw new ShopifyError(`Shopify could not process ${filename} (file ${file.id})`);
  onLog(`file ready ${file.id}`);
  return { id: file.id, url };
}

/* ------------------------------------------------------------------ */
/* Metaobject entry                                                    */
/* ------------------------------------------------------------------ */

export interface CreatedMetaobject { id: string; handle: string; adminUrl: string }

export async function createDraftMetaobject(fields: Record<string, string>, handle?: string, type = config.metaobjectType): Promise<CreatedMetaobject> {
  const fieldList = Object.entries(fields).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([key, value]) => ({ key, value }));
  const d = await graphql<{ metaobjectCreate: { metaobject: { id: string; handle: string } | null; userErrors: any[] } }>(
    `mutation($metaobject: MetaobjectCreateInput!) { metaobjectCreate(metaobject: $metaobject) { metaobject { id handle } userErrors { field message code } } }`,
    { metaobject: { type, handle, fields: fieldList, capabilities: { publishable: { status: 'DRAFT' } } } },
  );
  assertNoUserErrors('metaobjectCreate', d.metaobjectCreate.userErrors);
  const mo = d.metaobjectCreate.metaobject;
  if (!mo) throw new ShopifyError('metaobjectCreate returned no metaobject');
  return { id: mo.id, handle: mo.handle, adminUrl: adminUrlFor(mo.id, type) };
}

export function adminUrlFor(gid: string, type = config.metaobjectType): string {
  const numeric = gid.split('/').pop();
  return `https://admin.shopify.com/store/${config.storeHandle}/content/entries/${type}/${numeric}`;
}
