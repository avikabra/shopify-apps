import { useEffect, useState } from 'react';
import {
  Badge,
  Banner,
  BlockStack,
  Card,
  DataTable,
  InlineStack,
  Layout,
  Page,
  Spinner,
  Text,
} from '@shopify/polaris';
import { Link } from 'react-router-dom';
import { useApi } from '../lib/api';

interface DashboardStats {
  lowStockCount: number;
  lowStockItems: { sku: string; available: number; minQty: number; locationName?: string }[];
  openPurchaseOrders: number;
  inTransitTransfers: number;
  trackedInventoryValue: number;
  recentActivity: {
    id: string;
    type: string;
    refNumber?: string | null;
    sku: string;
    qty: number;
    locationName?: string | null;
    createdAt: string;
  }[];
}

function StatCard({ label, value, to }: { label: string; value: string | number; to: string }) {
  return (
    <Card>
      <BlockStack gap="100">
        <Text variant="bodyMd" as="p" tone="subdued">{label}</Text>
        <Link to={to} style={{ textDecoration: 'none' }}>
          <Text variant="heading2xl" as="p">{value}</Text>
        </Link>
      </BlockStack>
    </Card>
  );
}

export default function Dashboard() {
  const api = useApi();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<DashboardStats>('/api/dashboard')
      .then(setStats)
      .catch((e) => setError(e.message));
  }, [api]);

  return (
    <Page title="Dashboard">
      {error && (
        <Banner tone="critical" onDismiss={() => setError(null)}>
          {error}
        </Banner>
      )}
      {!stats && !error && <Spinner />}
      {stats && (
        <BlockStack gap="400">
          <Layout>
            <Layout.Section variant="oneHalf">
              <StatCard label="Items low on stock" value={stats.lowStockCount} to="/stock-alerts" />
            </Layout.Section>
            <Layout.Section variant="oneHalf">
              <StatCard label="Open purchase orders" value={stats.openPurchaseOrders} to="/purchase-orders" />
            </Layout.Section>
            <Layout.Section variant="oneHalf">
              <StatCard label="Transfers in transit" value={stats.inTransitTransfers} to="/transfers" />
            </Layout.Section>
            <Layout.Section variant="oneHalf">
              <StatCard
                label="Tracked inventory value"
                value={`$${stats.trackedInventoryValue.toLocaleString()}`}
                to="/stock-alerts"
              />
            </Layout.Section>
          </Layout>

          <Card>
            <BlockStack gap="200">
              <Text variant="headingMd" as="h2">Low stock right now</Text>
              {stats.lowStockItems.length === 0 ? (
                <Text tone="subdued" as="p">Nothing is below its minimum. Nice.</Text>
              ) : (
                <DataTable
                  columnContentTypes={['text', 'text', 'numeric', 'numeric']}
                  headings={['SKU', 'Location', 'On hand', 'Min']}
                  rows={stats.lowStockItems.map((i) => [
                    i.sku,
                    i.locationName ?? '—',
                    i.available,
                    i.minQty,
                  ])}
                />
              )}
              <InlineStack>
                <Link to="/stock-alerts">Manage stock alerts</Link>
              </InlineStack>
            </BlockStack>
          </Card>

          <Card>
            <BlockStack gap="200">
              <Text variant="headingMd" as="h2">Recent receiving activity</Text>
              {stats.recentActivity.length === 0 ? (
                <Text tone="subdued" as="p">No receiving activity yet.</Text>
              ) : (
                <DataTable
                  columnContentTypes={['text', 'text', 'text', 'numeric', 'text']}
                  headings={['Type', 'Reference', 'SKU', 'Qty', 'When']}
                  rows={stats.recentActivity.map((a) => [
                    a.type === 'PO_RECEIVE' ? (
                      <Badge key="t" tone="success">PO received</Badge>
                    ) : (
                      <Badge key="t" tone="info">Transfer</Badge>
                    ),
                    a.refNumber ?? '—',
                    a.sku,
                    (a.qty > 0 ? '+' : '') + a.qty,
                    new Date(a.createdAt).toLocaleString(),
                  ])}
                />
              )}
            </BlockStack>
          </Card>
        </BlockStack>
      )}
    </Page>
  );
}
