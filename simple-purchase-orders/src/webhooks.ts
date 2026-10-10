import { DeliveryMethod } from '@shopify/shopify-api';
import type { WebhookHandlersParam } from '@shopify/shopify-app-express';
import { prisma } from './db.js';
import { checkInstantAlert } from './services/alerts.js';

/**
 * Shop-specific webhook handlers. Declared here so the library reconciles
 * subscriptions on OAuth (any topic used must be listed).
 */
export const webhookHandlers: WebhookHandlersParam = {
  APP_UNINSTALLED: [
    {
      deliveryMethod: DeliveryMethod.Http,
      callbackUrl: '/api/webhooks',
      callback: async (_topic: string, shop: string, _body: string) => {
        // GDPR: erase all shop data promptly after uninstall.
        const existing = await prisma.shop.findUnique({ where: { domain: shop } });
        if (existing) {
          await prisma.shop.delete({ where: { id: existing.id } }); // cascades
        }
        await prisma.session.deleteMany({ where: { shop } });
      },
    },
  ],

  INVENTORY_LEVELS_UPDATE: [
    {
      deliveryMethod: DeliveryMethod.Http,
      callbackUrl: '/api/webhooks',
      callback: async (_topic: string, shop: string, body: string) => {
        try {
          const payload = JSON.parse(body) as { inventory_item_id?: number };
          if (payload.inventory_item_id) {
            await checkInstantAlert(
              shop,
              `gid://shopify/InventoryItem/${payload.inventory_item_id}`,
            );
          }
        } catch {
          // Never fail a webhook on alert evaluation.
        }
      },
    },
  ],

  // Mandatory privacy webhooks. We store no customer data, so these are
  // no-ops that acknowledge receipt.
  CUSTOMERS_DATA_REQUEST: [
    {
      deliveryMethod: DeliveryMethod.Http,
      callbackUrl: '/api/webhooks',
      callback: async () => {},
    },
  ],
  CUSTOMERS_REDACT: [
    {
      deliveryMethod: DeliveryMethod.Http,
      callbackUrl: '/api/webhooks',
      callback: async () => {},
    },
  ],
  SHOP_REDACT: [
    {
      deliveryMethod: DeliveryMethod.Http,
      callbackUrl: '/api/webhooks',
      callback: async () => {},
    },
  ],
};
