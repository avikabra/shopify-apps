import { useEffect, useState } from 'react';
import {
  Badge,
  Banner,
  BlockStack,
  Button,
  Card,
  InlineStack,
  Layout,
  Page,
  Spinner,
  Text,
} from '@shopify/polaris';
import { useApi } from '../lib/api';

interface PlanInfo {
  key: string;
  name: string;
  price: number;
  trialDays: number;
  skuLimitLabel: string;
  blurb: string;
  current: boolean;
}

interface BillingState {
  plan: string;
  plans: PlanInfo[];
  skusUsed: number;
  skuLimitLabel: string;
  subscription: { id: string; name: string; status: string } | null;
}

function priceLabel(p: PlanInfo): string {
  if (p.price === 0) return 'Free';
  const trial = p.trialDays > 0 ? `, ${p.trialDays}-day free trial` : '';
  return `$${p.price}/month${trial}`;
}

export default function Billing() {
  const api = useApi();
  const [state, setState] = useState<BillingState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = () => {
    api<BillingState>('/api/billing')
      .then(setState)
      .catch((e) => setError(e.message));
  };

  useEffect(load, [api]);

  const startPlan = (planKey: string) => {
    // Shopify's billing confirmation must open in the top frame.
    window.top!.location.href = `/api/billing/start?plan=${planKey}`;
  };

  const downgrade = async () => {
    setBusy('downgrade');
    setError(null);
    try {
      await api('/api/billing/downgrade', { method: 'POST' });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Downgrade failed');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Page title="Billing">
      <BlockStack gap="400">
        {error && (
          <Banner tone="critical" onDismiss={() => setError(null)}>
            {error}
          </Banner>
        )}
        {!state && !error && <Spinner />}
        {state && (
          <>
            <Card>
              <BlockStack gap="200">
                <InlineStack align="space-between" blockAlign="center">
                  <Text variant="headingMd" as="h2">
                    Current plan
                  </Text>
                  <Badge tone="info">
                    {state.plans.find((p) => p.key === state.plan)?.name ?? state.plan}
                  </Badge>
                </InlineStack>
                <Text variant="bodyMd" as="p" tone="subdued">
                  Tracking {state.skusUsed} of {state.skuLimitLabel} SKUs.
                </Text>
                {state.subscription && (
                  <Text variant="bodySm" as="p" tone="subdued">
                    Shopify subscription: {state.subscription.name} ({state.subscription.status})
                  </Text>
                )}
              </BlockStack>
            </Card>
            <Layout>
              {state.plans.map((p) => (
                <Layout.Section key={p.key} variant="oneThird">
                  <Card>
                    <BlockStack gap="300">
                      <InlineStack align="space-between" blockAlign="center">
                        <Text variant="headingMd" as="h3">
                          {p.name}
                        </Text>
                        {p.current && <Badge tone="success">Current</Badge>}
                      </InlineStack>
                      <Text variant="heading2xl" as="p">
                        {priceLabel(p)}
                      </Text>
                      <Text variant="bodyMd" as="p" tone="subdued">
                        {p.blurb}
                      </Text>
                      <Text variant="bodySm" as="p">
                        Up to {p.skuLimitLabel} SKUs
                      </Text>
                      {!p.current &&
                        (p.price === 0 ? (
                          <Button
                            onClick={downgrade}
                            loading={busy === 'downgrade'}
                            disabled={busy !== null}
                          >
                            Downgrade to Free
                          </Button>
                        ) : (
                          <Button
                            variant="primary"
                            onClick={() => startPlan(p.key)}
                            disabled={busy !== null}
                          >
                            Upgrade to {p.name}
                          </Button>
                        ))}
                    </BlockStack>
                  </Card>
                </Layout.Section>
              ))}
            </Layout>
            <Text variant="bodySm" as="p" tone="subdued">
              Paid plans are billed through Shopify every 30 days and include a{' '}
              {state.plans.find((p) => p.price > 0)?.trialDays ?? 14}-day free trial. Cancel
              anytime by downgrading to Free.
            </Text>
          </>
        )}
      </BlockStack>
    </Page>
  );
}
