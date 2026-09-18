/**
 * Encrypted, file-backed store for Shopify offline access tokens.
 * Tokens are encrypted with AES-256-GCM using a key derived from SESSION_SECRET,
 * so a copied data directory is useless without the secret. Swap this module
 * for a database (same interface) when running more than one instance.
 */
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from './config.js';

export interface ShopSession {
  shop: string;
  accessToken: string;
  scope: string;
  installedAt: string;
  updatedAt: string;
}

interface Encrypted { iv: string; tag: string; data: string }

function key(): Buffer {
  if (!config.sessionSecret || config.sessionSecret.length < 32) {
    throw new Error('SESSION_SECRET must be set (>= 32 random characters)');
  }
  return Buffer.from(crypto.hkdfSync('sha256', config.sessionSecret, 'smori-photo-assistant', 'shopify-token-store', 32));
}

function encrypt(plain: string): Encrypted {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key(), iv);
  const data = Buffer.concat([cipher.update(plain, 'utf8'), cipher.final()]);
  return { iv: iv.toString('base64'), tag: cipher.getAuthTag().toString('base64'), data: data.toString('base64') };
}

function decrypt(e: Encrypted): string {
  const decipher = crypto.createDecipheriv('aes-256-gcm', key(), Buffer.from(e.iv, 'base64'));
  decipher.setAuthTag(Buffer.from(e.tag, 'base64'));
  return Buffer.concat([decipher.update(Buffer.from(e.data, 'base64')), decipher.final()]).toString('utf8');
}

const file = () => path.join(config.dataDir, 'sessions.enc.json');

async function readAll(): Promise<Record<string, Encrypted>> {
  try { return JSON.parse(await fs.readFile(file(), 'utf8')); } catch { return {}; }
}

async function writeAll(all: Record<string, Encrypted>): Promise<void> {
  await fs.mkdir(config.dataDir, { recursive: true });
  const tmp = file() + '.tmp';
  await fs.writeFile(tmp, JSON.stringify(all, null, 2), { mode: 0o600 });
  await fs.rename(tmp, file());
}

export async function saveSession(s: Omit<ShopSession, 'installedAt' | 'updatedAt'>): Promise<ShopSession> {
  const all = await readAll();
  const existing = all[s.shop] ? (JSON.parse(decrypt(all[s.shop])) as ShopSession) : null;
  const now = new Date().toISOString();
  const session: ShopSession = { ...s, installedAt: existing?.installedAt ?? now, updatedAt: now };
  all[s.shop] = encrypt(JSON.stringify(session));
  await writeAll(all);
  return session;
}

export async function getSession(shop: string): Promise<ShopSession | null> {
  const all = await readAll();
  if (!all[shop]) return null;
  return JSON.parse(decrypt(all[shop])) as ShopSession;
}

export async function deleteSession(shop: string): Promise<boolean> {
  const all = await readAll();
  if (!all[shop]) return false;
  delete all[shop];
  await writeAll(all);
  return true;
}

export async function listShops(): Promise<string[]> {
  return Object.keys(await readAll());
}
