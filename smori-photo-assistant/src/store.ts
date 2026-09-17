import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { config } from './config.js';

export interface ProjectFacts {
  /** Confirmed by staff, never inferred from photos. */
  product_name: string;
  category: string;
  location: string;
  room?: string;
  room_zh?: string;
  installed_on?: string; // YYYY-MM-DD
  title?: string; // optional working title
  notes?: string; // anything staff wants the copy to reflect
}

export interface Photo {
  id: string;
  originalName: string;
  mime: string;
  originalBytes: number;
  webBytes: number;
  width: number;
  height: number;
  order: number;
  cover: boolean;
  caption?: string;
  caption_zh?: string;
  shopifyFileId?: string;
  shopifyUrl?: string;
}

export interface CopyBlock {
  type: 'heading' | 'paragraph' | 'list';
  text?: string;
  items?: string[];
}

export interface GeneratedCopy {
  title: string;
  title_zh: string;
  subtitle: string;
  subtitle_zh: string;
  summary: string;
  summary_zh: string;
  description: CopyBlock[];
  description_zh: CopyBlock[];
  room_observed?: string;
  room_observed_zh?: string;
  xhs_title: string;
  xhs_body: string;
  warnings: string[];
  model?: string;
  generatedAt?: string;
}

export interface ShopifyResult {
  metaobjectId: string;
  handle: string;
  status: 'DRAFT' | 'ACTIVE';
  adminUrl: string;
  createdAt: string;
  dryRun?: boolean;
}

export interface Project {
  id: string;
  createdAt: string;
  updatedAt: string;
  facts: ProjectFacts;
  photos: Photo[];
  copy?: GeneratedCopy;
  shopify?: ShopifyResult;
}

export const projectsDir = () => path.join(config.dataDir, 'projects');
export const projectDir = (id: string) => path.join(projectsDir(), id);
export const photoPath = (id: string, kind: 'original' | 'web' | 'thumb', file: string) =>
  path.join(projectDir(id), kind, file);

function safeId(id: string): string {
  if (!/^[a-z0-9-]{4,64}$/.test(id)) throw new Error(`invalid project id: ${id}`);
  return id;
}

export async function createProject(facts: ProjectFacts): Promise<Project> {
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const id = `${stamp}-${crypto.randomBytes(3).toString('hex')}`;
  const now = new Date().toISOString();
  const project: Project = { id, createdAt: now, updatedAt: now, facts, photos: [] };
  for (const kind of ['original', 'web', 'thumb']) {
    await fs.mkdir(path.join(projectDir(id), kind), { recursive: true });
  }
  await saveProject(project);
  return project;
}

export async function saveProject(project: Project): Promise<void> {
  project.updatedAt = new Date().toISOString();
  const file = path.join(projectDir(safeId(project.id)), 'project.json');
  const tmp = `${file}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(project, null, 2));
  await fs.rename(tmp, file);
}

export async function getProject(id: string): Promise<Project> {
  const raw = await fs.readFile(path.join(projectDir(safeId(id)), 'project.json'), 'utf8');
  const p = JSON.parse(raw) as Project;
  p.photos.sort((a, b) => a.order - b.order);
  return p;
}

export async function listProjects(): Promise<Project[]> {
  await fs.mkdir(projectsDir(), { recursive: true });
  const ids = await fs.readdir(projectsDir());
  const out: Project[] = [];
  for (const id of ids) {
    try { out.push(await getProject(id)); } catch { /* skip partial dirs */ }
  }
  return out.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export async function deletePhotoFiles(projectId: string, fileName: string): Promise<void> {
  for (const kind of ['original', 'web', 'thumb'] as const) {
    await fs.rm(photoPath(projectId, kind, fileName), { force: true });
  }
}
