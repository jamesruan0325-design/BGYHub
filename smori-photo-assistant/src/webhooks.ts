import type { Request, Response } from 'express';
import { verifyWebhookHmac } from './oauth.js';
import { deleteSession } from './tokens.js';

/** Express handler for app/uninstalled and the compliance topics. Body must be raw (express.raw). */
export async function handleWebhook(req: Request, res: Response): Promise<void> {
  const topic = String(req.headers['x-shopify-topic'] ?? '');
  const shop = String(req.headers['x-shopify-shop-domain'] ?? '');
  const raw = Buffer.isBuffer(req.body) ? req.body : Buffer.from('');
  if (!verifyWebhookHmac(raw, req.headers['x-shopify-hmac-sha256'] as string | undefined)) {
    res.status(401).send('invalid hmac');
    return;
  }
  switch (topic) {
    case 'app/uninstalled':
    case 'shop/redact':
      if (shop) await deleteSession(shop); // token is void after uninstall; drop it
      break;
    case 'customers/data_request':
    case 'customers/redact':
      // This app stores no customer data; nothing to return or erase.
      break;
  }
  console.log(`webhook ${topic} from ${shop || '?'} handled`);
  res.status(200).send('ok');
}
