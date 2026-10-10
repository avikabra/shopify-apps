import { useEffect, useState } from 'react';
import {
  Badge,
  Banner,
  Card,
  DataTable,
  Page,
  Select,
  Spinner,
  Text,
} from '@shopify/polaris';
import { Link } from 'react-router-dom';
import { useApi } from '../lib/api';

interface PurchaseOrderRow {
  id: string;
  poNumber: string;
  status: string;
  supplier: { name: string };
  _count: { lines: number };
  createdAt: string;
}

const STATUS_TONE: Record<string, 'info' | 'warning' | 'success' | 'critical' | 'attention'> = {
  DRAFT: 'info',
  SENT: 'attention',
  PARTIALLY_RECEIVED: 'warning',
  RECEIVED: 'success',
  CANCELLED: 'critical',
};

export function statusBadge(status: string) {
  return (
    <Badge tone={STATUS_TONE[status] ?? 'info'}>
      {status.replace(/_/g, ' ')}
    </Badge>
  );
}

export default function PurchaseOrders() {
  const api = useApi();
  const [orders, setOrders] = useState<PurchaseOrderRow[]>([]);
  const [status, setStatus] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api<PurchaseOrderRow[]>(`/api/purchase-orders${status ? `?status=${status}` : ''}`)
      .then((rows) => {
        setOrders(rows);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, [api, status]);

  return (
    <Page
      title="Purchase orders"
      primaryAction={{ content: 'New purchase order', url: '/purchase-orders/new' }}
    >
      {error && (
        <Banner tone="critical" onDismiss={() => setError(null)}>
          {error}
        </Banner>
      )}
      <Card>
        <div style={{ padding: '12px 16px', maxWidth: 320 }}>
          <Select
            label="Status"
            labelInline
            options={[
              { label: 'All', value: '' },
              { label: 'Draft', value: 'DRAFT' },
              { label: 'Sent', value: 'SENT' },
              { label: 'Partially received', value: 'PARTIALLY_RECEIVED' },
              { label: 'Received', value: 'RECEIVED' },
              { label: 'Cancelled', value: 'CANCELLED' },
            ]}
            value={status}
            onChange={setStatus}
          />
        </div>
        {loading ? (
          <div style={{ padding: 24 }}><Spinner /></div>
        ) : orders.length === 0 ? (
          <div style={{ padding: 24 }}>
            <Text tone="subdued" as="p">
              No purchase orders yet. Create one to start tracking incoming stock.
            </Text>
          </div>
        ) : (
          <DataTable
            columnContentTypes={['text', 'text', 'text', 'numeric', 'text', 'text']}
            headings={['PO number', 'Supplier', 'Status', 'Lines', 'Created', '']}
            rows={orders.map((o) => [
              o.poNumber,
              o.supplier.name,
              statusBadge(o.status),
              o._count.lines,
              new Date(o.createdAt).toLocaleDateString(),
              <Link key="v" to={`/purchase-orders/${o.id}`}>View</Link>,
            ])}
          />
        )}
      </Card>
    </Page>
  );
}
