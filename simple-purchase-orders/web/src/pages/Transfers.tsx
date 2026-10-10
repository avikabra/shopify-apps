import { useCallback, useEffect, useState } from 'react';
import {
  Autocomplete,
  Badge,
  Banner,
  BlockStack,
  Button,
  Card,
  DataTable,
  InlineStack,
  Modal,
  Page,
  Select,
  Spinner,
  Text,
  TextField,
} from '@shopify/polaris';
import { useApi } from '../lib/api';

interface TransferLine {
  inventoryItemId: string;
  sku: string;
  qty: number;
}
interface Transfer {
  id: string;
  transferNumber: string;
  fromLocationName: string;
  toLocationName: string;
  status: string;
  notes?: string | null;
  createdAt: string;
  _count: { lines: number };
}
interface Location {
  id: string;
  name: string;
  isActive: boolean;
}
interface Variant {
  inventoryItemId: string;
  sku: string;
  productTitle: string;
  variantTitle: string;
}

const STATUS_TONE: Record<string, 'info' | 'success' | 'critical'> = {
  IN_TRANSIT: 'info',
  RECEIVED: 'success',
  CANCELLED: 'critical',
};

export default function Transfers() {
  const api = useApi();
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);

  const [fromId, setFromId] = useState('');
  const [toId, setToId] = useState('');
  const [notes, setNotes] = useState('');
  const [lines, setLines] = useState<TransferLine[]>([]);
  const [search, setSearch] = useState('');
  const [options, setOptions] = useState<{ value: string; label: string }[]>([]);
  const [variantIndex, setVariantIndex] = useState<Record<string, Variant>>({});
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      api<Transfer[]>('/api/transfers'),
      api<{ locations: Location[] }>('/api/shop'),
    ])
      .then(([t, s]) => {
        setTransfers(t);
        setLocations(s.locations ?? []);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, [api]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (search.trim().length < 2) {
      setOptions([]);
      return;
    }
    const t = setTimeout(() => {
      api<Variant[]>(`/api/variants/search?q=${encodeURIComponent(search)}`)
        .then((variants) => {
          const idx: Record<string, Variant> = {};
          setOptions(
            variants.map((v) => {
              idx[v.inventoryItemId] = v;
              return {
                value: v.inventoryItemId,
                label: `${v.sku} — ${v.productTitle}${v.variantTitle ? ` (${v.variantTitle})` : ''}`,
              };
            }),
          );
          setVariantIndex(idx);
        })
        .catch(() => {});
    }, 250);
    return () => clearTimeout(t);
  }, [api, search]);

  const addLine = (inventoryItemId: string) => {
    const v = variantIndex[inventoryItemId];
    if (!v || lines.some((l) => l.inventoryItemId === inventoryItemId)) return;
    setLines([...lines, { inventoryItemId, sku: v.sku, qty: 1 }]);
    setSearch('');
    setOptions([]);
  };

  const create = async () => {
    setError(null);
    if (!fromId || !toId) return setError('Choose both locations');
    if (fromId === toId) return setError('Source and destination must be different');
    if (lines.length === 0) return setError('Add at least one line');
    setSaving(true);
    try {
      await api('/api/transfers', {
        method: 'POST',
        body: JSON.stringify({
          fromLocationId: fromId,
          toLocationId: toId,
          notes: notes || undefined,
          lines: lines.map((l) => ({ inventoryItemId: l.inventoryItemId, qty: l.qty })),
        }),
      });
      setModalOpen(false);
      setFromId('');
      setToId('');
      setNotes('');
      setLines([]);
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const transition = async (id: string, action: 'receive' | 'cancel') => {
    setBusyId(id);
    setError(null);
    try {
      await api(`/api/transfers/${id}/${action}`, { method: 'POST' });
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const locationOptions = (exclude?: string) =>
    locations
      .filter((l) => l.id !== exclude)
      .map((l) => ({ label: l.name, value: l.id }));

  return (
    <Page
      title="Stock transfers"
      primaryAction={{ content: 'New transfer', onAction: () => setModalOpen(true) }}
    >
      {error && (
        <Banner tone="critical" onDismiss={() => setError(null)}>
          {error}
        </Banner>
      )}
      <Card>
        {loading ? (
          <div style={{ padding: 24 }}><Spinner /></div>
        ) : transfers.length === 0 ? (
          <div style={{ padding: 24 }}>
            <Text tone="subdued" as="p">
              No transfers yet. Move stock between locations — the source is decremented
              immediately and the destination is incremented when you mark it received.
            </Text>
          </div>
        ) : (
          <DataTable
            columnContentTypes={['text', 'text', 'text', 'text', 'numeric', 'text', 'text']}
            headings={['Transfer', 'From', 'To', 'Status', 'Lines', 'Created', '']}
            rows={transfers.map((t) => [
              t.transferNumber,
              t.fromLocationName,
              t.toLocationName,
              <Badge key="s" tone={STATUS_TONE[t.status] ?? 'info'}>{t.status.replace(/_/g, ' ')}</Badge>,
              t._count.lines,
              new Date(t.createdAt).toLocaleDateString(),
              t.status === 'IN_TRANSIT' ? (
                <InlineStack key="a" gap="200">
                  <Button
                    size="slim"
                    variant="primary"
                    loading={busyId === t.id}
                    onClick={() => transition(t.id, 'receive')}
                  >
                    Mark received
                  </Button>
                  <Button size="slim" tone="critical" onClick={() => transition(t.id, 'cancel')}>
                    Cancel
                  </Button>
                </InlineStack>
              ) : (
                ''
              ),
            ])}
          />
        )}
      </Card>

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title="New stock transfer"
        primaryAction={{ content: 'Create transfer', onAction: create, loading: saving }}
        secondaryActions={[{ content: 'Cancel', onAction: () => setModalOpen(false) }]}
        size="large"
      >
        <Modal.Section>
          <BlockStack gap="300">
            <InlineStack gap="300">
              <div style={{ flex: 1 }}>
                <Select
                  label="From location"
                  options={[{ label: 'Select…', value: '' }, ...locationOptions(toId)]}
                  value={fromId}
                  onChange={setFromId}
                />
              </div>
              <div style={{ flex: 1 }}>
                <Select
                  label="To location"
                  options={[{ label: 'Select…', value: '' }, ...locationOptions(fromId)]}
                  value={toId}
                  onChange={setToId}
                />
              </div>
            </InlineStack>
            <TextField label="Notes" value={notes} onChange={setNotes} autoComplete="off" />
            <Autocomplete
              options={options}
              selected={[]}
              onSelect={(vals) => addLine(vals[0])}
              textField={
                <Autocomplete.TextField
                  label="Add by SKU"
                  value={search}
                  onChange={setSearch}
                  placeholder="Type at least 2 characters of a SKU…"
                  autoComplete="off"
                />
              }
            />
            {lines.length > 0 && (
              <DataTable
                columnContentTypes={['text', 'text', 'text']}
                headings={['SKU', 'Qty', '']}
                rows={lines.map((l, i) => [
                  l.sku,
                  <div key="q" style={{ width: 90 }}>
                    <TextField
                      label="Qty"
                      labelHidden
                      type="number"
                      min={1}
                      value={String(l.qty)}
                      onChange={(v) =>
                        setLines(lines.map((x, j) => (j === i ? { ...x, qty: Math.max(1, Number(v) || 1) } : x)))
                      }
                      autoComplete="off"
                    />
                  </div>,
                  <Button key="r" variant="plain" tone="critical" onClick={() => setLines(lines.filter((_, j) => j !== i))}>
                    Remove
                  </Button>,
                ])}
              />
            )}
          </BlockStack>
        </Modal.Section>
      </Modal>
    </Page>
  );
}
