import sharp from 'sharp';
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { photoPath, type Photo } from './store.js';

export const WEB_MAX_EDGE = 2000;   // long edge for the copy uploaded to Shopify / shown on the site
export const THUMB_MAX_EDGE = 480;
export const CLAUDE_MAX_EDGE = 1200; // what we send to the model (fewer tokens, same content)

const ACCEPTED = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/tiff']);

export interface ProcessedPhoto {
  photo: Photo;
  webPath: string;
}

/**
 * Stores the untouched original, then writes an EXIF-stripped, auto-rotated,
 * resized JPEG for the web and a thumbnail. Returns the Photo record.
 */
export async function processUpload(projectId: string, buffer: Buffer, originalName: string, order: number): Promise<ProcessedPhoto> {
  const meta = await sharp(buffer).metadata();
  const mime = meta.format ? `image/${meta.format}` : 'application/octet-stream';
  if (!ACCEPTED.has(mime)) {
    throw new Error(`Unsupported image format "${meta.format ?? 'unknown'}" for ${originalName}. Use JPG, PNG or WebP (iPhone: Settings > Camera > Formats > Most Compatible).`);
  }
  const id = crypto.randomBytes(4).toString('hex');
  const base = `${String(order + 1).padStart(2, '0')}-${id}`;
  const ext = mime === 'image/png' ? '.png' : mime === 'image/webp' ? '.webp' : mime === 'image/tiff' ? '.tif' : '.jpg';

  // 1. original, byte-for-byte
  await fs.writeFile(photoPath(projectId, 'original', base + ext), buffer);

  // 2. web copy: rotate() applies EXIF orientation, then all metadata (incl. GPS) is dropped
  const web = sharp(buffer).rotate().resize({ width: WEB_MAX_EDGE, height: WEB_MAX_EDGE, fit: 'inside', withoutEnlargement: true })
    .jpeg({ quality: 82, mozjpeg: true });
  const webInfo = await web.toFile(photoPath(projectId, 'web', base + '.jpg'));

  // 3. thumbnail
  await sharp(buffer).rotate().resize({ width: THUMB_MAX_EDGE, height: THUMB_MAX_EDGE, fit: 'inside', withoutEnlargement: true })
    .jpeg({ quality: 78, mozjpeg: true }).toFile(photoPath(projectId, 'thumb', base + '.jpg'));

  const photo: Photo = {
    id: base,
    originalName,
    mime,
    originalBytes: buffer.byteLength,
    webBytes: webInfo.size,
    width: webInfo.width,
    height: webInfo.height,
    order,
    cover: order === 0,
  };
  return { photo, webPath: photoPath(projectId, 'web', base + '.jpg') };
}

/** JPEG bytes sized for the model. */
export async function forClaude(webPath: string): Promise<Buffer> {
  return sharp(webPath).resize({ width: CLAUDE_MAX_EDGE, height: CLAUDE_MAX_EDGE, fit: 'inside', withoutEnlargement: true })
    .jpeg({ quality: 80 }).toBuffer();
}

export function webFileName(photo: Photo): string {
  return path.basename(photo.id) + '.jpg';
}
