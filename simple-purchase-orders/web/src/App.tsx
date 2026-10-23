import { Provider as AppBridgeProvider } from '@shopify/app-bridge-react';
import { AppProvider, Frame, Navigation } from '@shopify/polaris';
import enTranslations from '@shopify/polaris/locales/en.json';
import '@shopify/polaris/build/esm/styles.css';
import {
  BrowserRouter,
  Link,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import PurchaseOrders from './pages/PurchaseOrders';
import PurchaseOrderNew from './pages/PurchaseOrderNew';
import PurchaseOrderDetail from './pages/PurchaseOrderDetail';
import Suppliers from './pages/Suppliers';
import Transfers from './pages/Transfers';
import StockAlerts from './pages/StockAlerts';
import Billing from './pages/Billing';

/**
 * Lets Polaris components (e.g. Navigation items) navigate client-side so the
 * embedded ?shop=&host= query params are preserved.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function PolarisLink({ url, children, ...rest }: any) {
  if (url && /^https?:\/\//.test(url)) {
    // eslint-disable-next-line jsx-a11y/anchor-has-content
    return <a href={url} {...rest}>{children}</a>;
  }
  return <Link to={url ?? '/'} {...rest}>{children}</Link>;
}

function Nav() {
  const location = useLocation();
  return (
    <Navigation location={location.pathname}>
      <Navigation.Section
        items={[
          { label: 'Dashboard', url: '/', selected: location.pathname === '/' },
          {
            label: 'Purchase orders',
            url: '/purchase-orders',
            selected: location.pathname.startsWith('/purchase-orders'),
          },
          {
            label: 'Transfers',
            url: '/transfers',
            selected: location.pathname.startsWith('/transfers'),
          },
          {
            label: 'Stock alerts',
            url: '/stock-alerts',
            selected: location.pathname.startsWith('/stock-alerts'),
          },
          {
            label: 'Suppliers',
            url: '/suppliers',
            selected: location.pathname.startsWith('/suppliers'),
          },
          {
            label: 'Billing',
            url: '/billing',
            selected: location.pathname.startsWith('/billing'),
          },
        ]}
      />
    </Navigation>
  );
}

function Shell() {
  return (
    <AppProvider i18n={enTranslations} linkComponent={PolarisLink}>
      <BrowserRouter>
        <Frame navigation={<Nav />}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/purchase-orders" element={<PurchaseOrders />} />
            <Route path="/purchase-orders/new" element={<PurchaseOrderNew />} />
            <Route path="/purchase-orders/:id" element={<PurchaseOrderDetail />} />
            <Route path="/transfers" element={<Transfers />} />
            <Route path="/stock-alerts" element={<StockAlerts />} />
            <Route path="/suppliers" element={<Suppliers />} />
            <Route path="/billing" element={<Billing />} />
          </Routes>
        </Frame>
      </BrowserRouter>
    </AppProvider>
  );
}

export default function App({ apiKey, host }: { apiKey: string; host: string | null }) {
  // Local mock-dev mode has no App Bridge; render the app shell directly.
  if (apiKey === 'mock') return <Shell />;
  const config = { apiKey, host: host as string, forceRedirect: true };
  return (
    <AppBridgeProvider config={config}>
      <Shell />
    </AppBridgeProvider>
  );
}
