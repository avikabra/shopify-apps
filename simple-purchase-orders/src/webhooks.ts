import { DeliveryMethod } from '@shopify/shopify-api';
import type { WebhookHandlersParam } from '@shopify/shopify-app-express';
import { prisma } from './db.js';
import { checkInstantAlert } from './services/alerts.js';
import { normalizePlan, planFromSubscriptionName } from './lib/plans.js';

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

  APP_SUBSCRIPTIONS_UPDATE: [
    {
      deliveryMethod: DeliveryMethod.Http,
      callbackUrl: '/api/webhooks',
      callback: async (_topic: string, shop: string, body: string) => {
        // Keep the shop's plan in sync when the merchant upgrades, downgrades,
        // or cancels their AppSubscription in the Shopify admin.
        try {
          const payload = JSON.parse(body) as {
            app_subscription?: {
              admin_graphql_api_id?: string;
              name?: string;
              status?: string;
            };
          };
          const sub = payload.app_subscription;
          if (!sub) return;
          const existing = await prisma.shop.findUnique({ where: { domain: shop } });
          if (!existing) return;
          const plan = planFromSubscriptionName(sub.name) ?? normalizePlan(existing.plan);
          // A cancelled/expired subscription drops the shop back to free.
          const cancelled = sub.status === 'CANCELLED' || sub.status === 'EXPIRED';
          await prisma.shop.update({
            where: { id: existing.id },
            data: {
              plan: cancelled ? 'free' : plan,
              subscriptionId: cancelled ? null : (sub.admin_graphql_api_id ?? existing.subscriptionId),
              billingStatus: sub.status ?? existing.billingStatus,
              planUpdatedAt: new Date(),
            },
          });
        } catch (err) {
          console.error('[webhooks] app_subscriptions/update failed:', err);
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
