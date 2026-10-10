import { useCallback, useEffect, useState } from 'react';
import {
  Banner,
  BlockStack,
  Button,
  Card,
  DataTable,
  DescriptionList,
  InlineStack,
  Page,
  Spinner,
  Text,
  TextField,
} from '@shopify/polaris';
import { authenticatedFetch } from '@shopify/app-bridge-utils';
import { useAppBridge } from '@shopify/app-bridge-react';
import { useParams } from 'react-router-dom';
import { useApi } from '../lib/api';
import { statusBadge } from './PurchaseOrders';

interface PoLine {
  id: string;
  sku: string;
  productTitle?: string | null;
  variantTitle?: string | null;
  qtyOrdered: number;
  qtyReceived: number;
  unitCost?: string | null;
}
interface PurchaseOrder {
  id: string;
  poNumber: string;
  status: string;
  notes?: string | null;
  expectedAt?: string | null;
  locationName?: string | null;
  sentAt?: string | null;
  createdAt: string;
  supplier: { id: string; name: string; email?: string | null; phone?: string | null };
  lines: PoLine[];
}

export default function PurchaseOrderDetail() {
  const { id } = useParams();
  const api = useApi();
  const app = useAppBridge();
  const [po, setPo] = useState<PurchaseOrder | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [receiveQty, setReceiveQty] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    api<PurchaseOrder>(`/api/purchase-orders/${id}`)
      .then(setPo)
      .catch((e) => setError(e.message));
  }, [api, id]);

  useEffect(() => {
    load();
  }, [load]);

  const action = async (path: string, body?: unknown) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await api<PurchaseOrder>(path, {
        method: 'POST',
        body: body ? JSON.stringify(body) : undefined,
      });
      setPo(updated);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const downloadPdf = async () => {
    setError(null);
    try {
      const fetchFn = authenticatedFetch(app);
      const res = await fetchFn(`/api/purchase-orders/${id}/pdf`);
      if (!res.ok) throw new Error(`Download failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${po?.poNumber ?? 'purchase-order'}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const receivable = po && (po.status === 'SENT' || po.status === 'PARTIALLY_RECEIVED');

  return (
    <Page
      title={po ? po.poNumber : 'Purchase order'}
      backAction={{ content: 'Purchase orders', url: '/purchase-orders' }}
      titleMetadata={po ? statusBadge(po.status) : undefined}
      primaryAction={
        po?.status === 'DRAFT'
          ? { content: 'Send to supplier', onAction: () => action(`/api/purchase-orders/${id}/send`), loading: busy }
          : undefined
      }
      secondaryActions={[
        { content: 'Download PDF', onAction: downloadPdf, disabled: !po },
        ...(po && ['DRAFT', 'SENT', 'PARTIALLY_RECEIVED'].includes(po.status)
          ? [{ content: 'Cancel PO', destructive: true, onAction: () => action(`/api/purchase-orders/${id}/cancel`) }]
          : []),
      ]}
    >
      {error && (
        <Banner tone="critical" onDismiss={() => setError(null)}>
          {error}
        </Banner>
      )}
      {!po && !error && <Spinner />}
      {po && (
        <BlockStack gap="400">
          <Card>
            <DescriptionList
              items={[
                { term: 'Supplier', description: `${po.supplier.name}${po.supplier.email ? ` — ${po.supplier.email}` : ''}` },
                { term: 'Deliver to', description: po.locationName ?? '—' },
                { term: 'Expected', description: po.expectedAt ? new Date(po.expectedAt).toLocaleDateString() : '—' },
                { term: 'Sent', description: po.sentAt ? new Date(po.sentAt).toLocaleString() : 'Not sent yet' },
                { term: 'Notes', description: po.notes || '—' },
              ]}
            />
          </Card>

          <Card>
            <BlockStack gap="200">
              <Text variant="headingMd" as="h2">Lines</Text>
              <DataTable
                columnContentTypes={['text', 'text', 'numeric', 'numeric', 'numeric', 'text']}
                headings={['SKU', 'Item', 'Ordered', 'Received', 'Remaining', receivable ? 'Receive' : '']}
                rows={po.lines.map((l) => {
                  const remaining = l.qtyOrdered - l.qtyReceived;
                  return [
                    l.sku,
                    [l.productTitle, l.variantTitle].filter(Boolean).join(' — ') || '—',
                    l.qtyOrdered,
                    l.qtyReceived,
                    remaining,
                    receivable && remaining > 0 ? (
                      <InlineStack key="r" gap="200" blockAlign="center">
                        <div style={{ width: 80 }}>
                          <TextField
                            label="Qty"
                            labelHidden
                            type="number"
                            min={1}
                            max={remaining}
                            value={receiveQty[l.id] ?? String(remaining)}
                            onChange={(v) => setReceiveQty({ ...receiveQty, [l.id]: v })}
                            autoComplete="off"
                          />
                        </div>
                        <Button
                          size="slim"
                          loading={busy}
                          onClick={() =>
                            action(`/api/purchase-orders/${id}/receive`, {
                              lineId: l.id,
                              qty: Number(receiveQty[l.id] ?? remaining),
                            })
                          }
                        >
                          Receive
                        </Button>
                      </InlineStack>
                    ) : (
                      ''
                    ),
                  ];
                })}
              />
              {po.status === 'DRAFT' && (
                <Banner tone="info">
                  This PO is a draft. Sending it emails the PDF to {po.supplier.email || 'the supplier'} and moves it to Sent.
                </Banner>
              )}
            </BlockStack>
          </Card>
        </BlockStack>
      )}
    </Page>
  );
}
