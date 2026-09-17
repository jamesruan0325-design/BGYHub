/**
 * Minimal fake of the Shopify Admin GraphQL endpoint + staged upload target,
 * covering exactly the operations the pipeline uses. Test-only.
 */
import http from 'node:http';
import { AddressInfo } from 'node:net';

export interface FakeState {
  definition: null | { id: string; type: string; fields: any[]; publishable: boolean; onlineStore: boolean };
  files: Map<string, { status: string; polls: number; alt: string; filename: string }>;
  uploads: { filename: string; bytes: number }[];
  metaobjects: { id: string; handle: string; type: string; fields: Record<string, string>; status: string }[];
  requests: string[];
}

export async function startFakeShopify(): Promise<{ endpoint: string; state: FakeState; close: () => Promise<void> }> {
  const state: FakeState = { definition: null, files: new Map(), uploads: [], metaobjects: [], requests: [] };
  let seq = 100;
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const body = Buffer.concat(chunks);
      if (req.url === '/upload') {
        const m = /filename="([^"]+)"/.exec(body.toString('latin1'));
        state.uploads.push({ filename: m?.[1] ?? '?', bytes: body.byteLength });
        res.writeHead(201).end();
        return;
      }
      const { query, variables } = JSON.parse(body.toString()) as { query: string; variables: any };
      const op = /(?:query|mutation)?\s*(?:\([^)]*\))?\s*{\s*(\w+)/.exec(query)?.[1] ?? /{\s*(\w+)/.exec(query)?.[1] ?? '?';
      state.requests.push(op);
      const reply = (data: unknown) => res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ data }));
      switch (op) {
        case 'shop': return reply({ shop: { name: 'Fake SMORI', myshopifyDomain: 'fake.myshopify.com' } });
        case 'metaobjectDefinitionByType':
          return reply({ metaobjectDefinitionByType: state.definition && state.definition.type === variables.type ? { id: state.definition.id, type: state.definition.type, fieldDefinitions: state.definition.fields.map((f) => ({ key: f.key, type: { name: f.type } })), capabilities: { publishable: { enabled: state.definition.publishable }, onlineStore: { enabled: state.definition.onlineStore } } } : null });
        case 'metaobjectDefinitionCreate': {
          const d = variables.definition;
          state.definition = { id: 'gid://shopify/MetaobjectDefinition/1', type: d.type, fields: d.fieldDefinitions, publishable: Boolean(d.capabilities?.publishable?.enabled), onlineStore: Boolean(d.capabilities?.onlineStore?.enabled) };
          return reply({ metaobjectDefinitionCreate: { metaobjectDefinition: { id: state.definition.id }, userErrors: [] } });
        }
        case 'metaobjectDefinitionUpdate': {
          for (const opn of variables.definition.fieldDefinitions ?? []) if (opn.create) state.definition!.fields.push(opn.create);
          return reply({ metaobjectDefinitionUpdate: { metaobjectDefinition: { id: state.definition!.id }, userErrors: [] } });
        }
        case 'stagedUploadsCreate': {
          const port = (server.address() as AddressInfo).port;
          return reply({ stagedUploadsCreate: { stagedTargets: variables.input.map((i: any) => ({ url: `http://127.0.0.1:${port}/upload`, resourceUrl: `https://fake-cdn/tmp/${i.filename}`, parameters: [{ name: 'key', value: 'tmp/' + i.filename }] })), userErrors: [] } });
        }
        case 'fileCreate': {
          const files = variables.files.map((f: any) => { const id = `gid://shopify/MediaImage/${++seq}`; state.files.set(id, { status: 'UPLOADED', polls: 0, alt: f.alt, filename: f.filename }); return { id, fileStatus: 'UPLOADED' }; });
          return reply({ fileCreate: { files, userErrors: [] } });
        }
        case 'node': {
          const f = state.files.get(variables.id)!; f.polls++; if (f.polls >= 2) f.status = 'READY';
          return reply({ node: { fileStatus: f.status, image: f.status === 'READY' ? { url: `https://fake-cdn/files/${f.filename}` } : null } });
        }
        case 'metaobjectCreate': {
          const m = variables.metaobject;
          if (!state.definition || state.definition.type !== m.type) return reply({ metaobjectCreate: { metaobject: null, userErrors: [{ field: ['type'], message: 'definition not found', code: 'NOT_FOUND' }] } });
          const known = new Set(state.definition.fields.map((f) => f.key));
          const bad = m.fields.filter((f: any) => !known.has(f.key));
          if (bad.length) return reply({ metaobjectCreate: { metaobject: null, userErrors: bad.map((f: any) => ({ field: ['fields', f.key], message: 'unknown field', code: 'INVALID' })) } });
          const id = `gid://shopify/Metaobject/${++seq}`;
          const handle = m.handle ?? `entry-${seq}`;
          state.metaobjects.push({ id, handle, type: m.type, fields: Object.fromEntries(m.fields.map((f: any) => [f.key, f.value])), status: m.capabilities?.publishable?.status ?? 'ACTIVE' });
          return reply({ metaobjectCreate: { metaobject: { id, handle }, userErrors: [] } });
        }
        default:
          return res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ errors: [{ message: `fake: unknown operation ${op}` }] }));
      }
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const port = (server.address() as AddressInfo).port;
  return { endpoint: `http://127.0.0.1:${port}/graphql`, state, close: () => new Promise((r) => server.close(() => r())) };
}
