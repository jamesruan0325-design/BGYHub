import 'dotenv/config';
import path from 'node:path';

function env(name: string, fallback = ''): string {
  const v = process.env[name];
  return v === undefined || v === '' ? fallback : v;
}

export const SCOPES = 'read_files,write_files,read_metaobjects,write_metaobjects,read_metaobject_definitions,write_metaobject_definitions';

export const config = {
  shop: env('SHOPIFY_SHOP', 'smori-9216.myshopify.com'),
  /** Legacy fallback only: a static Admin API token. The OAuth session store is preferred. */
  adminToken: env('SHOPIFY_ADMIN_TOKEN'),
  /** Set by `shopify app env pull` / `shopify app dev`, or by hand from the Dev Dashboard. */
  apiKey: env('SHOPIFY_API_KEY'),
  apiSecret: env('SHOPIFY_API_SECRET'),
  scopes: env('SCOPES', SCOPES),
  /** Public base URL of this deployment, e.g. https://smori-photo-assistant.fly.dev (no trailing slash). */
  appUrl: env('SHOPIFY_APP_URL', env('APP_URL', env('HOST'))).replace(/\/$/, ''),
  /** Random secret for cookie signing and token encryption (>= 32 chars). */
  sessionSecret: env('SESSION_SECRET'),
  nodeEnv: env('NODE_ENV', 'development'),
  apiVersion: env('SHOPIFY_API_VERSION', '2026-07'),
  storeHandle: env('SHOPIFY_STORE_HANDLE', 'smori-9216'),
  /** Optional override of the Admin GraphQL endpoint (used by the fake server in tests). */
  adminEndpoint: env('SHOPIFY_ADMIN_ENDPOINT'),
  claudeModel: env('CLAUDE_MODEL', 'claude-opus-5'),
  hasAnthropicKey: Boolean(process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN),
  port: Number(env('PORT', '3000')),
  dataDir: path.resolve(env('DATA_DIR', './data')),
  adminPassword: env('ADMIN_PASSWORD'),
  metaobjectType: env('METAOBJECT_TYPE', 'installation_case'),
};

export const CATEGORIES = [
  { value: 'sheer', label: 'Sheer & Light', zh: '透光柔纱' },
  { value: 'blackout', label: 'Blackout', zh: '遮光' },
  { value: 'signature', label: 'Signature', zh: '经典系列' },
  { value: 'motorized', label: 'Motorized', zh: '电动智能' },
  { value: 'drapery', label: 'Drapery', zh: '布艺窗帘' },
  { value: 'shutters', label: 'Shutters', zh: '百叶窗' },
  { value: 'commercial', label: 'Commercial', zh: '商业项目' },
] as const;
export type Category = (typeof CATEGORIES)[number]['value'];

/** The product lines shown on the S. MORI homepage. Used for the dropdown and as product knowledge for copywriting. */
export const PRODUCTS = [
  { name: 'Silhouette', subtitle: 'Sheer Shadings', category: 'sheer', notes: 'Signature S-shaped fabric vanes float between two sheers; balances light, privacy and UV protection.' },
  { name: 'Luminette', subtitle: 'Privacy Sheers', category: 'sheer', notes: 'Vertical fabric vanes rotate within a sheer; ideal for wide windows and sliding doors.' },
  { name: 'Duette', subtitle: 'Honeycomb Shades', category: 'blackout', notes: 'Cellular construction traps air for insulation; available in light-filtering and room-darkening opacities.' },
  { name: 'Pirouette', subtitle: 'Shadings', category: 'sheer', notes: 'Soft horizontal fabric vanes on a single sheer backing; flatten for a clean look or open for filtered light.' },
  { name: 'Vignette', subtitle: 'Modern Roman Shades', category: 'signature', notes: 'Clean, consistent folds with no exposed rear cords; flat and rolling styles.' },
  { name: 'PowerView', subtitle: 'Motorization', category: 'motorized', notes: 'Motorization controlled by app, voice or schedules; can be added to most Hunter Douglas products.' },
  { name: 'Designer Roller', subtitle: 'Roller Shades', category: 'blackout', notes: 'Roller shades with hundreds of fabric options from sheer to opaque; clean modern aesthetic.' },
  { name: 'Alustra', subtitle: 'Woven Textures', category: 'signature', notes: 'Luxury collection with exclusive fabrics, hardware finishes and design details.' },
  { name: 'Custom Drapery', subtitle: 'Drapery', category: 'drapery', notes: 'Made-to-measure drapery panels, sheers and layered treatments with custom hardware.' },
] as const;
