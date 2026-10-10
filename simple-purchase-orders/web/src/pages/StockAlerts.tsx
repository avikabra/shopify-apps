import { useCallback, useEffect, useState } from 'react';
import {
  Autocomplete,
  Banner,
  BlockStack,
  Button,
  Card,
  Checkbox,
  DataTable,
  InlineStack,
  Page,
  Select,
  Spinner,
  Text,
  TextField,
} from '@shopify/polaris';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../lib/api';

interface Threshold {
  id: string;
  inventoryItemId: string;
  sku: string;
  productTitle?: string | null;
  variantTitle?: string | null;
  locationId: string;
  locationName?: string | null;
  minQty: number;
  available?: number;
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
interface LowStockItem {
  inventoryItemId: string;
  sku: string;
  productTitle?: string;
  variantTitle?: string;
  locationName?: string;
  available: number;
  minQty: number;
}
interface Settings {
  digestEnabled: boolean;
  digestEmail?: string | null;
  digestHourUtc: number;
  instantAlerts: boolean;
}

const ALL = 'ALL_LOCATIONS';

export default function StockAlerts() {
  const api = useApi();
  const navigate = useNavigate();
  const [thresholds, setThresholds] = useState<Threshold[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [lowStock, setLowStock] = useState<LowStockItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);

  // Add-threshold form state
  const [search, setSearch] = useState('');
  const [options, setOptions] = useState<{ value: string; label: string }[]>([]);
  const [variantIndex, setVariantIndex] = useState<Record<string, Variant>>({});
  const [picked, setPicked] = useState<Variant | null>(null);
  const [locId, setLocId] = useState(ALL);
  const [minQty, setMinQty] = useState('10');
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      api<Threshold[]>('/api/thresholds'),
      api<{ locations: Location[] }>('/api/shop'),
      api<Settings>('/api/settings'),
    ])
      .then(([t, s, st]) => {
        setThresholds(t);
        setLocations(s.locations ?? []);
        setSettings(st);
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

  const addThreshold = async () => {
    if (!picked) return setError('Search and pick a variant first');
    const min = Number(minQty);
    if (!Number.isInteger(min) || min < 0) return setError('Minimum must be a whole number');
    setAdding(true);
    setError(null);
    try {
      const created = await api<Threshold>('/api/thresholds', {
        method: 'POST',
        body: JSON.stringify({ inventoryItemId: picked.inventoryItemId, locationId: locId, minQty: min }),
      });
      setThresholds([...thresholds, created]);
      setPicked(null);
      setSearch('');
      setMinQty('10');
      setLocId(ALL);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAdding(false);
    }
  };

  const updateMin = async (id: string, min: number) => {
    try {
      const updated = await api<Threshold>(`/api/thresholds/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ minQty: min }),
      });
      setThresholds(thresholds.map((t) => (t.id === id ? updated : t)));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const deleteThreshold = async (id: string) => {
    try {
      await api(`/api/thresholds/${id}`, { method: 'DELETE' });
      setThresholds(thresholds.filter((t) => t.id !== id));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const checkNow = async () => {
    setChecking(true);
    setError(null);
    try {
      const items = await api<LowStockItem[]>('/api/low-stock');
      setLowStock(items);
      load(); // refresh the "available" column
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  };

  const saveSettings = async () => {
    if (!settings) return;
    setSavingSettings(true);
    setError(null);
    try {
      const updated = await api<Settings>('/api/settings', {
        method: 'PUT',
        body: JSON.stringify(settings),
      });
      setSettings(updated);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSavingSettings(false);
    }
  };

  const createPoFromLowStock = () => {
    if (!lowStock || lowStock.length === 0) return;
    navigate('/purchase-orders/new', {
      state: {
        preselected: lowStock.map((i) => ({
          inventoryItemId: i.inventoryItemId,
          sku: i.sku,
          qty: Math.max(1, i.minQty - i.available + 1),
        })),
      },
    });
  };

  return (
    <Page
      title="Stock alerts"
      primaryAction={{ content: 'Check now', onAction: checkNow, loading: checking }}
    >
      {error && (
        <Banner tone="critical" onDismiss={() => setError(null)}>
          {error}
        </Banner>
      )}
      {lowStock && (
        <div style={{ marginBottom: 16 }}>
          <Banner
            tone={lowStock.length ? 'warning' : 'success'}
            onDismiss={() => setLowStock(null)}
          >
            {lowStock.length === 0
              ? 'Nothing is below its minimum right now.'
              : `${lowStock.length} item${lowStock.length === 1 ? '' : 's'} at or below minimum: ${lowStock.map((i) => `${i.sku} (${i.available})`).join(', ')}`}
          </Banner>
          {lowStock.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <Button variant="primary" onClick={createPoFromLowStock}>
                Create purchase order from these items
              </Button>
            </div>
          )}
        </div>
      )}

      {loading ? (
        <Spinner />
      ) : (
        <BlockStack gap="400">
          <Card>
            <BlockStack gap="300">
              <Text variant="headingMd" as="h2">Minimum stock levels</Text>
              {thresholds.length === 0 ? (
                <Text tone="subdued" as="p">
                  No alerts configured. Add a minimum per SKU and get a daily email digest
                  when stock runs low.
                </Text>
              ) : (
                <DataTable
                  columnContentTypes={['text', 'text', 'text', 'numeric', 'numeric', 'text']}
                  headings={['SKU', 'Item', 'Location', 'On hand', 'Min', '']}
                  rows={thresholds.map((t) => [
                    t.sku,
                    [t.productTitle, t.variantTitle].filter(Boolean).join(' — ') || '—',
                    t.locationId === ALL ? 'All locations' : (t.locationName ?? '—'),
                    t.available ?? '—',
                    <div key="m" style={{ width: 90 }}>
                      <TextField
                        label="Min"
                        labelHidden
                        type="number"
                        min={0}
                        value={String(t.minQty)}
                        onChange={(v) => {
                          const n = Number(v);
                          if (Number.isInteger(n) && n >= 0) updateMin(t.id, n);
                        }}
                        autoComplete="off"
                      />
                    </div>,
                    <Button key="d" variant="plain" tone="critical" onClick={() => deleteThreshold(t.id)}>
                      Remove
                    </Button>,
                  ])}
                />
              )}
            </BlockStack>
          </Card>

          <Card>
            <BlockStack gap="300">
              <Text variant="headingMd" as="h2">Add an alert</Text>
              {picked ? (
                <InlineStack gap="200" blockAlign="center">
                  <Text as="p">
                    <strong>{picked.sku}</strong> — {picked.productTitle}
                    {picked.variantTitle ? ` (${picked.variantTitle})` : ''}
                  </Text>
                  <Button variant="plain" onClick={() => setPicked(null)}>Change</Button>
                </InlineStack>
              ) : (
                <Autocomplete
                  options={options}
                  selected={[]}
                  onSelect={(vals) => {
                    setPicked(variantIndex[vals[0]] ?? null);
                    setSearch('');
                    setOptions([]);
                  }}
                  textField={
                    <Autocomplete.TextField
                      label="Find a variant"
                      value={search}
                      onChange={setSearch}
                      placeholder="Type at least 2 characters of a SKU…"
                      autoComplete="off"
                    />
                  }
                />
              )}
              <InlineStack gap="300">
                <div style={{ minWidth: 220 }}>
                  <Select
                    label="Location"
                    options={[
                      { label: 'All locations (total)', value: ALL },
                      ...locations.map((l) => ({ label: l.name, value: l.id })),
                    ]}
                    value={locId}
                    onChange={setLocId}
                  />
                </div>
                <div style={{ width: 140 }}>
                  <TextField
                    label="Alert when at or below"
                    type="number"
                    min={0}
                    value={minQty}
                    onChange={setMinQty}
                    autoComplete="off"
                  />
                </div>
                <div style={{ alignSelf: 'flex-end' }}>
                  <Button variant="primary" onClick={addThreshold} loading={adding} disabled={!picked}>
                    Add alert
                  </Button>
                </div>
              </InlineStack>
            </BlockStack>
          </Card>

          {settings && (
            <Card>
              <BlockStack gap="300">
                <Text variant="headingMd" as="h2">Email digest</Text>
                <Checkbox
                  label="Send me a daily low-stock digest"
                  checked={settings.digestEnabled}
                  onChange={(v) => setSettings({ ...settings, digestEnabled: v })}
                />
                <InlineStack gap="300">
                  <div style={{ minWidth: 260 }}>
                    <TextField
                      label="Digest email"
                      type="email"
                      value={settings.digestEmail ?? ''}
                      onChange={(v) => setSettings({ ...settings, digestEmail: v })}
                      placeholder="you@yourstore.com"
                      autoComplete="off"
                      helpText="Defaults to the shop email when empty"
                    />
                  </div>
                  <div style={{ width: 160 }}>
                    <Select
                      label="Send at (UTC hour)"
                      options={Array.from({ length: 24 }, (_, h) => ({
                        label: `${String(h).padStart(2, '0')}:00 UTC`,
                        value: String(h),
                      }))}
                      value={String(settings.digestHourUtc)}
                      onChange={(v) => setSettings({ ...settings, digestHourUtc: Number(v) })}
                    />
                  </div>
                </InlineStack>
                <Checkbox
                  label="Instant alerts (email immediately when a tracked item drops below its minimum)"
                  checked={settings.instantAlerts}
                  onChange={(v) => setSettings({ ...settings, instantAlerts: v })}
                />
                <InlineStack>
                  <Button variant="primary" onClick={saveSettings} loading={savingSettings}>
                    Save settings
                  </Button>
                </InlineStack>
              </BlockStack>
            </Card>
          )}
        </BlockStack>
      )}
    </Page>
  );
}
