import { useEffect, useState } from 'react';
import {
  Autocomplete,
  Banner,
  BlockStack,
  Button,
  Card,
  DataTable,
  InlineStack,
  Page,
  Select,
  Text,
  TextField,
} from '@shopify/polaris';
import { useLocation, useNavigate } from 'react-router-dom';
import { useApi } from '../lib/api';

interface Supplier {
  id: string;
  name: string;
  email?: string | null;
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
interface Line {
  inventoryItemId: string;
  sku: string;
  label: string;
  qty: number;
  unitCost?: string;
}
interface Preselected {
  inventoryItemId: string;
  sku: string;
  qty: number;
}

export default function PurchaseOrderNew() {
  const api = useApi();
  const navigate = useNavigate();
  const location = useLocation();
  const preselected = (location.state as { preselected?: Preselected[] } | null)?.preselected ?? [];

  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [supplierId, setSupplierId] = useState('');
  const [locationId, setLocationId] = useState('');
  const [notes, setNotes] = useState('');
  const [expectedAt, setExpectedAt] = useState('');
  const [lines, setLines] = useState<Line[]>(
    preselected.map((p) => ({
      inventoryItemId: p.inventoryItemId,
      sku: p.sku,
      label: p.sku,
      qty: p.qty,
    })),
  );
  const [search, setSearch] = useState('');
  const [options, setOptions] = useState<{ value: string; label: string }[]>([]);
  const [variantIndex, setVariantIndex] = useState<Record<string, Variant>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api<{ suppliers: Supplier[] }>('/api/suppliers')
      .then((d) => {
        const list = Array.isArray(d) ? d : d.suppliers ?? d;
        setSuppliers(Array.isArray(list) ? list : []);
      })
      .catch((e) => setError(e.message));
    api<{ locations: Location[] }>('/api/shop')
      .then((d) => setLocations(d.locations ?? []))
      .catch((e) => setError(e.message));
  }, [api]);

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
              const label = `${v.sku} — ${v.productTitle}${v.variantTitle ? ` (${v.variantTitle})` : ''}`;
              return { value: v.inventoryItemId, label };
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
    setLines([
      ...lines,
      {
        inventoryItemId,
        sku: v.sku,
        label: `${v.sku} — ${v.productTitle}${v.variantTitle ? ` (${v.variantTitle})` : ''}`,
        qty: 1,
      },
    ]);
    setSearch('');
    setOptions([]);
  };

  const save = async () => {
    setError(null);
    if (!supplierId) return setError('Choose a supplier');
    if (lines.length === 0) return setError('Add at least one line');
    setSaving(true);
    try {
      const po = await api<{ id: string }>('/api/purchase-orders', {
        method: 'POST',
        body: JSON.stringify({
          supplierId,
          locationId: locationId || undefined,
          notes: notes || undefined,
          expectedAt: expectedAt || undefined,
          lines: lines.map((l) => ({
            inventoryItemId: l.inventoryItemId,
            qtyOrdered: l.qty,
            unitCost: l.unitCost ? Number(l.unitCost) : undefined,
          })),
        }),
      });
      navigate(`/purchase-orders/${po.id}`);
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  };

  return (
    <Page
      title="New purchase order"
      backAction={{ content: 'Purchase orders', url: '/purchase-orders' }}
      primaryAction={{ content: 'Create draft', onAction: save, loading: saving }}
    >
      {error && (
        <Banner tone="critical" onDismiss={() => setError(null)}>
          {error}
        </Banner>
      )}
      <BlockStack gap="400">
        <Card>
          <BlockStack gap="300">
            <Select
              label="Supplier"
              options={[{ label: 'Select a supplier…', value: '' }, ...suppliers.map((s) => ({ label: s.name, value: s.id }))]}
              value={supplierId}
              onChange={setSupplierId}
            />
            <Select
              label="Receiving location"
              options={[{ label: 'Default (first active location)', value: '' }, ...locations.map((l) => ({ label: l.name, value: l.id }))]}
              value={locationId}
              onChange={setLocationId}
            />
            <TextField label="Expected date" type="date" value={expectedAt} onChange={setExpectedAt} autoComplete="off" />
            <TextField label="Notes" value={notes} onChange={setNotes} multiline={3} autoComplete="off" />
          </BlockStack>
        </Card>

        <Card>
          <BlockStack gap="300">
            <Text variant="headingMd" as="h2">Lines</Text>
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
                columnContentTypes={['text', 'text', 'text', 'text']}
                headings={['Item', 'Qty', 'Unit cost', '']}
                rows={lines.map((l, i) => [
                  l.label,
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
                  <div key="c" style={{ width: 110 }}>
                    <TextField
                      label="Unit cost"
                      labelHidden
                      type="number"
                      prefix="$"
                      value={l.unitCost ?? ''}
                      onChange={(v) => setLines(lines.map((x, j) => (j === i ? { ...x, unitCost: v } : x)))}
                      autoComplete="off"
                    />
                  </div>,
                  <Button key="r" variant="plain" tone="critical" onClick={() => setLines(lines.filter((_, j) => j !== i))}>
                    Remove
                  </Button>,
                ])}
              />
            )}
            <InlineStack align="end">
              <Button variant="primary" onClick={save} loading={saving}>
                Create draft
              </Button>
            </InlineStack>
          </BlockStack>
        </Card>
      </BlockStack>
    </Page>
  );
}
