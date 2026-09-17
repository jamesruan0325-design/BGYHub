import type { CopyBlock } from './store.js';

/** Shopify rich_text_field JSON (root > heading | paragraph | list > list-item > text). */
export interface RichTextNode {
  type: string;
  value?: string;
  level?: number;
  listType?: 'ordered' | 'unordered';
  bold?: boolean;
  italic?: boolean;
  children?: RichTextNode[];
}

export function blocksToRichText(blocks: CopyBlock[]): RichTextNode {
  const children: RichTextNode[] = [];
  for (const b of blocks) {
    if (b.type === 'heading' && b.text?.trim()) {
      children.push({ type: 'heading', level: 2, children: [{ type: 'text', value: b.text.trim() }] });
    } else if (b.type === 'paragraph' && b.text?.trim()) {
      children.push({ type: 'paragraph', children: [{ type: 'text', value: b.text.trim() }] });
    } else if (b.type === 'list' && b.items?.length) {
      children.push({
        type: 'list',
        listType: 'unordered',
        children: b.items.filter((i) => i.trim()).map((i) => ({ type: 'list-item', children: [{ type: 'text', value: i.trim() }] })),
      });
    }
  }
  return { type: 'root', children };
}

/**
 * Plain-text editing format used by the UI:
 *   "## Heading"  -> heading
 *   "- item"      -> list item (consecutive items form one list)
 *   blank line    -> paragraph break
 */
export function blocksToText(blocks: CopyBlock[]): string {
  const out: string[] = [];
  for (const b of blocks) {
    if (b.type === 'heading') out.push(`## ${b.text ?? ''}`);
    else if (b.type === 'paragraph') out.push(b.text ?? '');
    else if (b.type === 'list') out.push((b.items ?? []).map((i) => `- ${i}`).join('\n'));
  }
  return out.join('\n\n');
}

export function textToBlocks(text: string): CopyBlock[] {
  const blocks: CopyBlock[] = [];
  const chunks = text.replace(/\r/g, '').split(/\n\s*\n/);
  for (const chunk of chunks) {
    const lines = chunk.split('\n').map((l) => l.trim()).filter(Boolean);
    if (!lines.length) continue;
    let para: string[] = [];
    let list: string[] = [];
    const flushPara = () => { if (para.length) { blocks.push({ type: 'paragraph', text: para.join(' ') }); para = []; } };
    const flushList = () => { if (list.length) { blocks.push({ type: 'list', items: list }); list = []; } };
    for (const line of lines) {
      if (/^#{1,3}\s+/.test(line)) { flushPara(); flushList(); blocks.push({ type: 'heading', text: line.replace(/^#{1,3}\s+/, '') }); }
      else if (/^[-*•]\s+/.test(line)) { flushPara(); list.push(line.replace(/^[-*•]\s+/, '')); }
      else { flushList(); para.push(line); }
    }
    flushPara(); flushList();
  }
  return blocks;
}

export function blocksToHtml(blocks: CopyBlock[]): string {
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return blocks.map((b) => {
    if (b.type === 'heading') return `<h2>${esc(b.text ?? '')}</h2>`;
    if (b.type === 'list') return `<ul>${(b.items ?? []).map((i) => `<li>${esc(i)}</li>`).join('')}</ul>`;
    return `<p>${esc(b.text ?? '')}</p>`;
  }).join('\n');
}
