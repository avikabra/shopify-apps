import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { fetchConfig } from './lib/api';

function Boot() {
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchConfig()
      .then((c) => setApiKey(c.apiKey))
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return (
      <div style={{ padding: 40, fontFamily: 'system-ui' }}>
        <h2>Could not start Simple Purchase Orders</h2>
        <p>{error}</p>
      </div>
    );
  }
  if (!apiKey) {
    return <div style={{ padding: 40, fontFamily: 'system-ui' }}>Loading…</div>;
  }

  const host = new URLSearchParams(window.location.search).get('host');
  // Local mock-dev mode (apiKey 'mock') runs outside the Shopify admin.
  if (!host && apiKey !== 'mock') {
    return (
      <div style={{ padding: 40, fontFamily: 'system-ui' }}>
        <h2>Simple Purchase Orders</h2>
        <p>Open this app from your Shopify admin to get started.</p>
      </div>
    );
  }
  return <App apiKey={apiKey} host={host} />;
}

createRoot(document.getElementById('root')!).render(<Boot />);
