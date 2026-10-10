import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { config } from '../config.js';

// ---------------------------------------------------------------------------
// Adapter interface — the single seam between the app and Shopify.
// ---------------------------------------------------------------------------

export interface LocationInfo {
  id: string;
  name: string;
  isActive: boolean;
}

export interface LevelInfo {
  inventoryItemId: string;
  locationId: string;
  locationName: string;
  available: number;
  onHand: number;
}

export interface VariantInfo {
  inventoryItemId: string;
  sku: string;
  productTitle: string;
  variantTitle: string;
  unitCost: number | null;
}

export interface InventoryChange {
  inventoryItemId: string;
  locationId: string;
  delta: number;
}

export type AdjustmentReason = 'received' | 'correction';

export interface ShopifyInventoryClient {
  getLocations(): Promise<LocationInfo[]>;
  getLevels(inventoryItemIds: string[]): Promise<LevelInfo[]>;
  getVariants(inventoryItemIds: string[]): Promise<VariantInfo[]>;
  /** Search variants by SKU substring (for the threshold-setup UI). */
  searchVariants(skuSubstring: string): Promise<VariantInfo[]>;
  adjust(
    changes: InventoryChange[],
    reason: AdjustmentReason,
    referenceDocumentUri?: string,
  ): Promise<void>;
}

// ---------------------------------------------------------------------------
// Low-level GraphQL helper (also reused by the billing module).
// ---------------------------------------------------------------------------

export async function graphqlAdmin<T>(
  shop: string,
  accessToken: string,
  query: string,
  variables?: Record<string, unknown>,
): Promise<T> {
  const res = await fetch(
    `https://${shop}/admin/api/${config.apiVersion}/graphql.json`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Shopify-Access-Token': accessToken,
      },
      body: JSON.stringify({ query, variables }),
    },
  );
  if (!res.ok) {
    throw new Error(`Shopify API HTTP ${res.status}: ${await res.text()}`);
  }
  const json = (await res.json()) as {
    data?: T;
    errors?: { message: string }[];
  };
  if (json.errors?.length) {
    throw new Error(
      `Shopify GraphQL errors: ${json.errors.map((e) => e.message).join('; ')}`,
    );
  }
  return json.data as T;
}

// ---------------------------------------------------------------------------
// Real implementation (Admin GraphQL).
// ---------------------------------------------------------------------------

const LOCATIONS_QUERY = `
  query Locations {
    locations(first: 50) {
      nodes { id name isActive }
    }
  }
`;

const LEVELS_QUERY = `
  query Levels($ids: [ID!]!) {
    nodes(ids: $ids) {
      ... on InventoryItem {
        id
        inventoryLevels(first: 25) {
          nodes {
            location { id name }
            quantities(names: ["available", "on_hand"]) { name quantity }
          }
        }
      }
    }
  }
`;

const VARIANTS_QUERY = `
  query Variants($ids: [ID!]!) {
    nodes(ids: $ids) {
      ... on InventoryItem {
        id
        sku
        unitCost { amount }
        variant { title product { title } }
      }
    }
  }
`;

const SEARCH_QUERY = `
  query SearchVariants($query: String!) {
    products(first: 10, query: $query) {
      nodes {
        title
        variants(first: 25) {
          nodes {
            title
            sku
            inventoryItem { id }
          }
        }
      }
    }
  }
`;

const ADJUST_MUTATION = `
  mutation Adjust($input: InventoryAdjustQuantitiesInput!) {
    inventoryAdjustQuantities(input: $input) {
      inventoryAdjustmentGroup { createdAt reason }
      userErrors { field message code }
    }
  }
`;

interface UserError {
  field?: string[] | null;
  message: string;
  code?: string | null;
}

function throwOnUserErrors(userErrors: UserError[] | undefined, action: string): void {
  if (userErrors?.length) {
    throw new Error(
      `Shopify ${action} failed: ${userErrors.map((e) => e.message).join('; ')}`,
    );
  }
}

export class GraphqlShopifyClient implements ShopifyInventoryClient {
  constructor(
    private shop: string,
    private accessToken: string,
  ) {}

  async getLocations(): Promise<LocationInfo[]> {
    const data = await graphqlAdmin<{
      locations: { nodes: { id: string; name: string; isActive: boolean }[] };
    }>(this.shop, this.accessToken, LOCATIONS_QUERY);
    return data.locations.nodes;
  }

  async getLevels(inventoryItemIds: string[]): Promise<LevelInfo[]> {
    if (inventoryItemIds.length === 0) return [];
    const out: LevelInfo[] = [];
    // nodes() accepts up to 250 ids; chunk conservatively.
    for (const chunk of chunkOf(inventoryItemIds, 100)) {
      const data = await graphqlAdmin<{
        nodes: {
          id: string;
          inventoryLevels: {
            nodes: {
              location: { id: string; name: string };
              quantities: { name: string; quantity: number }[];
            }[];
          };
        }[];
      }>(this.shop, this.accessToken, LEVELS_QUERY, { ids: chunk });
      for (const node of data.nodes ?? []) {
        if (!node) continue;
        for (const lvl of node.inventoryLevels.nodes) {
          const q = Object.fromEntries(
            lvl.quantities.map((x) => [x.name, x.quantity]),
          );
          out.push({
            inventoryItemId: node.id,
            locationId: lvl.location.id,
            locationName: lvl.location.name,
            available: q['available'] ?? 0,
            onHand: q['on_hand'] ?? 0,
          });
        }
      }
    }
    return out;
  }

  async getVariants(inventoryItemIds: string[]): Promise<VariantInfo[]> {
    if (inventoryItemIds.length === 0) return [];
    const out: VariantInfo[] = [];
    for (const chunk of chunkOf(inventoryItemIds, 100)) {
      const data = await graphqlAdmin<{
        nodes: {
          id: string;
          sku: string;
          unitCost: { amount: string } | null;
          variant: { title: string; product: { title: string } } | null;
        }[];
      }>(this.shop, this.accessToken, VARIANTS_QUERY, { ids: chunk });
      for (const node of data.nodes ?? []) {
        if (!node) continue;
        out.push({
          inventoryItemId: node.id,
          sku: node.sku ?? '',
          productTitle: node.variant?.product.title ?? '',
          variantTitle: node.variant && node.variant.title !== 'Default Title'
            ? node.variant.title
            : '',
          unitCost: node.unitCost ? Number(node.unitCost.amount) : null,
        });
      }
    }
    return out;
  }

  async searchVariants(skuSubstring: string): Promise<VariantInfo[]> {
    const q = skuSubstring.trim();
    if (!q) return [];
    const data = await graphqlAdmin<{
      products: {
        nodes: {
          title: string;
          variants: {
            nodes: { title: string; sku: string; inventoryItem: { id: string } | null }[];
          };
        }[];
      };
    }>(this.shop, this.accessToken, SEARCH_QUERY, { query: `sku:*${q}*` });
    const out: VariantInfo[] = [];
    for (const product of data.products.nodes) {
      for (const v of product.variants.nodes) {
        if (!v.inventoryItem) continue;
        out.push({
          inventoryItemId: v.inventoryItem.id,
          sku: v.sku ?? '',
          productTitle: product.title,
          variantTitle: v.title !== 'Default Title' ? v.title : '',
          unitCost: null,
        });
      }
    }
    return out;
  }

  async adjust(
    changes: InventoryChange[],
    reason: AdjustmentReason,
    referenceDocumentUri?: string,
  ): Promise<void> {
    if (changes.length === 0) return;
    const data = await graphqlAdmin<{
      inventoryAdjustQuantities: {
        userErrors: UserError[];
      };
    }>(this.shop, this.accessToken, ADJUST_MUTATION, {
      input: {
        name: 'available',
        reason,
        ...(referenceDocumentUri ? { referenceDocumentUri } : {}),
        changes: changes.map((c) => ({
          inventoryItemId: c.inventoryItemId,
          locationId: c.locationId,
          delta: c.delta,
        })),
      },
    });
    throwOnUserErrors(data.inventoryAdjustQuantities.userErrors, 'inventory adjustment');
  }
}

// ---------------------------------------------------------------------------
// Mock implementation (local fixtures — no Shopify credentials needed).
// ---------------------------------------------------------------------------

interface MockLevelMap {
  [locationId: string]: { available: number; onHand: number };
}

interface MockVariant {
  inventoryItemId: string;
  sku: string;
  productTitle: string;
  variantTitle: string;
  unitCost: number | null;
  levels: MockLevelMap;
}

interface MockShopData {
  locations: { id: string; name: string; isActive: boolean }[];
  variants: MockVariant[];
}

export class MockShopifyClient implements ShopifyInventoryClient {
  private data: MockShopData;
  private path: string;

  constructor(dataPath = config.mockDataPath) {
    this.path = dataPath;
    if (!existsSync(this.path)) {
      throw new Error(
        `Mock Shopify data file not found at ${this.path}. ` +
          `Copy data/mock-shop.json into place or set MOCK_DATA_PATH.`,
      );
    }
    this.data = JSON.parse(readFileSync(this.path, 'utf8')) as MockShopData;
  }

  private save(): void {
    mkdirSync(dirname(this.path), { recursive: true });
    writeFileSync(this.path, JSON.stringify(this.data, null, 2));
  }

  async getLocations(): Promise<LocationInfo[]> {
    return this.data.locations;
  }

  async getLevels(inventoryItemIds: string[]): Promise<LevelInfo[]> {
    const out: LevelInfo[] = [];
    for (const v of this.data.variants) {
      if (!inventoryItemIds.includes(v.inventoryItemId)) continue;
      for (const loc of this.data.locations) {
        const lvl = v.levels[loc.id] ?? { available: 0, onHand: 0 };
        out.push({
          inventoryItemId: v.inventoryItemId,
          locationId: loc.id,
          locationName: loc.name,
          available: lvl.available,
          onHand: lvl.onHand,
        });
      }
    }
    return out;
  }

  async getVariants(inventoryItemIds: string[]): Promise<VariantInfo[]> {
    return this.data.variants
      .filter((v) => inventoryItemIds.includes(v.inventoryItemId))
      .map((v) => ({
        inventoryItemId: v.inventoryItemId,
        sku: v.sku,
        productTitle: v.productTitle,
        variantTitle: v.variantTitle,
        unitCost: v.unitCost,
      }));
  }

  async searchVariants(skuSubstring: string): Promise<VariantInfo[]> {
    const q = skuSubstring.trim().toLowerCase();
    if (!q) return [];
    return this.data.variants
      .filter((v) => v.sku.toLowerCase().includes(q))
      .slice(0, 25)
      .map((v) => ({
        inventoryItemId: v.inventoryItemId,
        sku: v.sku,
        productTitle: v.productTitle,
        variantTitle: v.variantTitle,
        unitCost: v.unitCost,
      }));
  }

  async adjust(
    changes: InventoryChange[],
    _reason: AdjustmentReason,
    _referenceDocumentUri?: string,
  ): Promise<void> {
    for (const c of changes) {
      const v = this.data.variants.find(
        (x) => x.inventoryItemId === c.inventoryItemId,
      );
      if (!v) throw new Error(`Mock: unknown inventory item ${c.inventoryItemId}`);
      const lvl = v.levels[c.locationId] ?? { available: 0, onHand: 0 };
      const next = lvl.available + c.delta;
      if (next < 0) {
        throw new Error(
          `Mock: insufficient stock for ${v.sku} (have ${lvl.available}, delta ${c.delta})`,
        );
      }
      v.levels[c.locationId] = { available: next, onHand: lvl.onHand + c.delta };
    }
    this.save();
  }
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function clientForShop(
  shop: string,
  accessToken?: string,
): ShopifyInventoryClient {
  if (config.mockShopify || !accessToken) {
    return new MockShopifyClient();
  }
  return new GraphqlShopifyClient(shop, accessToken);
}

function chunkOf<T>(arr: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}
