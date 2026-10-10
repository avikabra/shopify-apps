import { useCallback, useEffect, useState } from 'react';
import {
  Autocomplete,
  Banner,
  BlockStack,
  Button,
  Card,
  DataTable,
  InlineStack,
  Modal,
  Page,
  Spinner,
  Text,
  TextField,
} from '@shopify/polaris';
import { useApi } from '../lib/api';

interface SupplierSku {
  id: string;
  sku: string;
  variantTitle?: string | null;
}
interface Supplier {
  id: string;
  name: string;
  contactName?: string | null;
  email?: string | null;
  phone?: string | null;
  notes?: string | null;
  skus: SupplierSku[];
}
interface Variant {
  inventoryItemId: string;
  sku: string;
  productTitle: string;
  variantTitle: string;
}

const EMPTY: Omit<Supplier, 'id' | 'skus'> = {
  name: '',
  contactName: '',
  email: '',
  phone: '',
  notes: '',
};

export default function Suppliers() {
  const api = useApi();
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [editing, setEditing] = useState<(typeof EMPTY & { id?: string }) | null>(null);
  const [managing, setManaging] = useState<Supplier | null>(null);
  const [skuSearch, setSkuSearch] = useState('');
  const [skuOptions, setSkuOptions] = useState<{ value: string; label: string }[]>([]);
  const [variantIndex, setVariantIndex] = useState<Record<string, Variant>>({});
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api<Supplier[]>('/api/suppliers')
      .then((rows) => {
        setSuppliers(rows);
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
    if (skuSearch.trim().length < 2) {
      setSkuOptions([]);
      return;
    }
    const t = setTimeout(() => {
      api<Variant[]>(`/api/variants/search?q=${encodeURIComponent(skuSearch)}`)
        .then((variants) => {
          const idx: Record<string, Variant> = {};
          setSkuOptions(
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
  }, [api, skuSearch]);

  const saveSupplier = async () => {
    if (!editing || !editing.name.trim()) {
      setError('Supplier name is required');
      return;
    }
    setSaving(true);
    try {
      const body = {
        name: editing.name.trim(),
        contactName: editing.contactName || undefined,
        email: editing.email || undefined,
        phone: editing.phone || undefined,
        notes: editing.notes || undefined,
      };
      if (editing.id) {
        await api(`/api/suppliers/${editing.id}`, { method: 'PUT', body: JSON.stringify(body) });
      } else {
        await api('/api/suppliers', { method: 'POST', body: JSON.stringify(body) });
      }
      setEditing(null);
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const deleteSupplier = async (id: string) => {
    if (!window.confirm('Delete this supplier? Purchase orders are kept.')) return;
    try {
      await api(`/api/suppliers/${id}`, { method: 'DELETE' });
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const addSku = async (inventoryItemId: string) => {
    if (!managing) return;
    const v = variantIndex[inventoryItemId];
    if (!v) return;
    try {
      const updated = await api<Supplier>(`/api/suppliers/${managing.id}/skus`, {
        method: 'POST',
        body: JSON.stringify({ inventoryItemId }),
      });
      setManaging(updated);
      setSkuSearch('');
      setSkuOptions([]);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const removeSku = async (skuId: string) => {
    if (!managing) return;
    try {
      const updated = await api<Supplier>(`/api/suppliers/${managing.id}/skus/${skuId}`, {
        method: 'DELETE',
      });
      setManaging(updated);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Page
      title="Suppliers"
      primaryAction={{ content: 'Add supplier', onAction: () => setEditing({ ...EMPTY }) }}
    >
      {error && (
        <Banner tone="critical" onDismiss={() => setError(null)}>
          {error}
        </Banner>
      )}
      <Card>
        {loading ? (
          <div style={{ padding: 24 }}><Spinner /></div>
        ) : suppliers.length === 0 ? (
          <div style={{ padding: 24 }}>
            <Text tone="subdued" as="p">No suppliers yet. Add the vendors you order stock from.</Text>
          </div>
        ) : (
          <DataTable
            columnContentTypes={['text', 'text', 'text', 'numeric', 'text']}
            headings={['Name', 'Contact', 'Email', 'SKUs', '']}
            rows={suppliers.map((s) => [
              s.name,
              s.contactName || '—',
              s.email || '—',
              s.skus.length,
              <InlineStack key="a" gap="200">
                <Button variant="plain" onClick={() => setManaging(s)}>SKUs</Button>
                <Button variant="plain" onClick={() => setEditing({ ...s })}>Edit</Button>
                <Button variant="plain" tone="critical" onClick={() => deleteSupplier(s.id)}>Delete</Button>
              </InlineStack>,
            ])}
          />
        )}
      </Card>

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title={editing?.id ? 'Edit supplier' : 'Add supplier'}
        primaryAction={{ content: 'Save', onAction: saveSupplier, loading: saving }}
        secondaryActions={[{ content: 'Cancel', onAction: () => setEditing(null) }]}
      >
        <Modal.Section>
          {editing && (
            <BlockStack gap="300">
              <TextField label="Name" value={editing.name} onChange={(v) => setEditing({ ...editing, name: v })} autoComplete="off" />
              <TextField label="Contact name" value={editing.contactName ?? ''} onChange={(v) => setEditing({ ...editing, contactName: v })} autoComplete="off" />
              <TextField label="Email" type="email" value={editing.email ?? ''} onChange={(v) => setEditing({ ...editing, email: v })} autoComplete="off" helpText="Used to email purchase orders" />
              <TextField label="Phone" value={editing.phone ?? ''} onChange={(v) => setEditing({ ...editing, phone: v })} autoComplete="off" />
              <TextField label="Notes" value={editing.notes ?? ''} onChange={(v) => setEditing({ ...editing, notes: v })} multiline={2} autoComplete="off" />
            </BlockStack>
          )}
        </Modal.Section>
      </Modal>

      <Modal
        open={!!managing}
        onClose={() => setManaging(null)}
        title={managing ? `SKUs supplied by ${managing.name}` : 'SKUs'}
        secondaryActions={[{ content: 'Done', onAction: () => setManaging(null) }]}
      >
        <Modal.Section>
          <BlockStack gap="300">
            <Autocomplete
              options={skuOptions}
              selected={[]}
              onSelect={(vals) => addSku(vals[0])}
              textField={
                <Autocomplete.TextField
                  label="Add a SKU"
                  value={skuSearch}
                  onChange={setSkuSearch}
                  placeholder="Type at least 2 characters of a SKU…"
                  autoComplete="off"
                />
              }
            />
            {managing && managing.skus.length > 0 ? (
              <DataTable
                columnContentTypes={['text', 'text', 'text']}
                headings={['SKU', 'Variant', '']}
                rows={managing.skus.map((s) => [
                  s.sku,
                  s.variantTitle || '—',
                  <Button key="r" variant="plain" tone="critical" onClick={() => removeSku(s.id)}>Remove</Button>,
                ])}
              />
            ) : (
              <Text tone="subdued" as="p">No SKUs linked yet. Linking SKUs makes reordering faster.</Text>
            )}
          </BlockStack>
        </Modal.Section>
      </Modal>
    </Page>
  );
}
