import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Button, Caption1, Card, CardHeader, CounterBadge, Divider, Field,
  MessageBar, MessageBarActions, MessageBarBody, Select, Spinner,
  Tab, TabList, Text, Title1, Title2,
  Toolbar, ToolbarButton, ToolbarDivider, ToolbarGroup,
  makeStyles, tokens,
} from '@fluentui/react-components';
import {
  ArrowClockwise20Regular, Apps20Regular, Dismiss20Regular, Key20Regular,
  List20Regular, People20Regular, SignOut20Regular, Warning20Regular,
} from '@fluentui/react-icons';
import icon32 from './assets/icon-32.png';
import iconObsidian32 from './assets/icon-obsidian-32.png';
import Login from './components/Login.jsx';
import QuotaTable from './components/QuotaTable.jsx';
import CallTable from './components/CallTable.jsx';
import BalanceHistory from './components/BalanceHistory.jsx';
import KeyManagement from './components/KeyManagement.jsx';
import Alerts from './components/Alerts.jsx';
import LanguageSwitcher from './components/LanguageSwitcher.jsx';
import ThemeMenu from './components/ThemeMenu.jsx';
import { useI18n } from './i18n.jsx';
import { useThemeMode } from './theme.jsx';

const useStyles = makeStyles({
  workspace: {
    minHeight: '100vh',
    display: 'grid',
    gridTemplateColumns: '236px minmax(0, 1fr)',
    backgroundColor: tokens.colorNeutralBackground2,
    '@media (max-width: 900px)': {
      gridTemplateColumns: 'minmax(0, 1fr)',
    },
  },
  sidebar: {
    position: 'sticky',
    top: 0,
    height: '100vh',
    display: 'flex',
    flexDirection: 'column',
    rowGap: tokens.spacingVerticalM,
    padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalM}`,
    backgroundColor: tokens.colorNeutralBackground1,
    borderRight: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke2}`,
    minWidth: 0,
    '@media (max-width: 900px)': {
      position: 'static',
      height: 'auto',
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      flexWrap: 'wrap',
      rowGap: tokens.spacingVerticalS,
      borderRight: 'none',
      borderBottom: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke2}`,
      padding: tokens.spacingHorizontalM,
    },
  },
  brand: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    paddingBottom: tokens.spacingVerticalL,
    color: tokens.colorNeutralForeground1,
    textDecorationLine: 'none',
    '@media (max-width: 900px)': {
      paddingBottom: 0,
    },
  },
  brandIcon: {
    width: '42px',
    height: '42px',
    borderRadius: tokens.borderRadiusMedium,
    boxShadow: tokens.shadow2,
    flexShrink: 0,
  },
  brandText: {
    fontSize: tokens.fontSizeBase400,
    fontWeight: tokens.fontWeightSemibold,
    lineHeight: tokens.lineHeightBase400,
  },
  nav: {
    flexGrow: 1,
    minWidth: 0,
    maxWidth: '100%',
    '@media (max-width: 900px)': {
      flexGrow: 0,
      width: '100%',
      flexWrap: 'wrap',
      overflowX: 'visible',
    },
  },
  note: {
    color: tokens.colorNeutralForeground3,
    paddingInline: tokens.spacingHorizontalS,
    '@media (max-width: 900px)': {
      display: 'none',
    },
  },
  main: {
    minWidth: 0,
    width: '100%',
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr)',
    alignContent: 'start',
    rowGap: tokens.spacingVerticalL,
    padding: `${tokens.spacingVerticalXL} ${tokens.spacingHorizontalXXL} ${tokens.spacingVerticalXXL}`,
    '@media (max-width: 900px)': {
      padding: tokens.spacingHorizontalL,
    },
  },
  header: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalL,
    flexWrap: 'wrap',
    minWidth: 0,
    '@media (max-width: 900px)': {
      flexDirection: 'column',
      alignItems: 'stretch',
    },
  },
  titleBlock: {
    display: 'grid',
    rowGap: tokens.spacingVerticalXS,
  },
  description: {
    color: tokens.colorNeutralForeground2,
  },
  actions: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    flexWrap: 'wrap',
    minWidth: 0,
    maxWidth: '100%',
    '@media (max-width: 900px)': {
      width: '100%',
    },
  },
  summary: {
    display: 'grid',
    gap: tokens.spacingHorizontalM,
    gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
    minWidth: 0,
  },
  statCard: {
    rowGap: tokens.spacingVerticalXS,
  },
  statIcon: {
    color: tokens.colorBrandForeground1,
    display: 'flex',
  },
  panel: {
    rowGap: tokens.spacingVerticalM,
    minWidth: 0,
  },
  panelFilters: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    flexWrap: 'wrap',
    minWidth: 0,
    maxWidth: '100%',
    '@media (max-width: 900px)': {
      width: '100%',
      justifyContent: 'flex-start',
    },
  },
  updated: {
    display: 'block',
    color: tokens.colorNeutralForeground3,
  },
});

export default function App() {
  const { t, formatDateTime, intlLocale } = useI18n();
  const { isDark } = useThemeMode();
  const styles = useStyles();
  const [data, setData] = useState(null);
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [provider, setProvider] = useState('');
  const [tab, setTab] = useState('overview');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState('');
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 620px)').matches);
  const token = useRef('');
  const active = useRef(null);
  const sequence = useRef(0);

  const logout = useCallback(() => {
    sequence.current += 1;
    active.current?.abort();
    token.current = '';
    setData(null); setError(''); setLoading(false); setProvider(''); setSyncMessage('');
  }, []);

  const load = useCallback(async (selectedMonth, quiet = false) => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const id = ++sequence.current;
    if (!quiet) setLoading(true);
    setError('');
    try {
      const response = await fetch(`/admin/api/overview?month=${encodeURIComponent(selectedMonth)}`, {
        headers: { Authorization: `Bearer ${token.current}` }, cache: 'no-store', credentials: 'omit', signal: controller.signal,
      });
      if (response.status === 401) {
        logout(); setError(t('login.invalidToken')); return false;
      }
      if (!response.ok) throw new Error(response.status === 429 ? t('login.tooMany') : t('login.readFailed', { status: response.status }));
      const payload = await response.json();
      if (id !== sequence.current) return false;
      setData(payload); return true;
    } catch (failure) {
      if (failure.name !== 'AbortError' && id === sequence.current) setError(failure.message);
      return false;
    } finally {
      if (id === sequence.current) setLoading(false);
    }
  }, [logout, t]);

  useEffect(() => {
    if (token.current) void load(month);
  }, [month, load]);
  useEffect(() => () => { active.current?.abort(); }, []);
  useEffect(() => {
    if (!data) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible' && token.current) void load(month, true);
    }, 30000);
    return () => window.clearInterval(timer);
  }, [Boolean(data), month, load]);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 620px)');
    const changed = event => setMobile(event.matches);
    media.addEventListener('change', changed);
    return () => media.removeEventListener('change', changed);
  }, []);

  const monthOptions = useMemo(() => {
    const now = new Date();
    return Array.from({ length: 12 }, (_, index) => {
      const date = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - index, 1));
      return {
        value: date.toISOString().slice(0, 7),
        label: new Intl.DateTimeFormat(intlLocale, { year: 'numeric', month: 'long', timeZone: 'UTC' }).format(date),
      };
    });
  }, [intlLocale]);

  async function login(value) { token.current = value; return load(month); }
  if (!data) return <Login onLogin={login} loading={loading} error={error} />;

  const accounts = data.accounts.filter(account => !provider || account.provider === provider);
  const accountIds = new Set(accounts.map(account => account.id));
  const keys = data.keys.filter(key => accountIds.has(key.account_id));
  const calls = data.calls.filter(call => !provider || call.provider === provider);
  const traces = (data.traces || []).filter(trace => !provider || trace.providers.includes(provider));
  const providers = [...new Set(data.accounts.map(account => account.provider))].sort();
  const relevantIds = new Set([...accountIds, ...keys.map(key => key.id), ...data.pools.filter(pool => accountIds.has(pool.account_id)).map(pool => pool.id)]);
  const alerts = (data.alerts || []).filter(alert => relevantIds.has(alert.scope_id));
  const pendingAlerts = alerts.filter(alert => alert.dismissed_at == null);
  const stats = [
    { label: t('stats.accounts'), id: 'account-count', value: accounts.length, icon: <People20Regular /> },
    { label: t('stats.keys'), id: 'key-count', value: keys.length, icon: <Key20Regular /> },
    { label: t('stats.attention'), id: 'attention-count', value: pendingAlerts.length, icon: <Warning20Regular /> },
  ];

  async function readTrace(id) {
    const sessionToken = token.current;
    try {
      const response = await fetch(`/admin/api/traces/${encodeURIComponent(id)}`, {
        headers: { Authorization: `Bearer ${sessionToken}` }, credentials: 'omit', cache: 'no-store',
      });
      if (token.current !== sessionToken) return { ok: false, error: t('login.stateChanged') };
      if (response.status === 401) { logout(); return { ok: false, error: t('login.relogin') }; }
      if (!response.ok) return { ok: false, error: response.status === 404 ? t('error.traceMissing') : t('error.traceReadFailed') };
      return { ok: true, data: await response.json() };
    } catch { return { ok: false, error: t('error.connectionRetry') }; }
  }

  async function syncBalances() {
    setSyncing(true); setSyncMessage('');
    const sessionToken = token.current;
    try {
      const response = await fetch('/admin/api/balances/sync', {
        method: 'POST', headers: { Authorization: `Bearer ${sessionToken}`, 'Content-Type': 'application/json' },
        credentials: 'omit', cache: 'no-store', body: JSON.stringify({ account_ids: accounts.map(account => account.id) }),
      });
      if (token.current !== sessionToken) return;
      if (response.status === 401) { logout(); return; }
      if (!response.ok) throw new Error(t('error.syncFailed', { status: response.status }));
      const { results } = await response.json();
      const ok = results.filter(result => result.status === 'ok').length;
      const failed = results.filter(result => result.status === 'failed').length;
      const limited = results.filter(result => result.status === 'rate_limited').length;
      const skipped = results.length - ok - failed - limited;
      setSyncMessage(t('sync.result', { ok, failed, limited, skipped }));
      await load(month);
    } catch (failure) {
      if (token.current === sessionToken) setSyncMessage(failure.message);
    } finally { setSyncing(false); }
  }

  async function mutate(route, method, body) {
    const sessionToken = token.current;
    try {
      const response = await fetch(route, { method, headers: { Authorization: `Bearer ${sessionToken}`, 'Content-Type': 'application/json', 'X-Oracle-Action': 'manage' },
        credentials: 'omit', cache: 'no-store', body: JSON.stringify({ ...body, revision: body.revision ?? data.revision }) });
      if (token.current !== sessionToken) return { ok: false, error: t('login.stateChanged') };
      if (response.status === 401) { logout(); return { ok: false, error: t('login.relogin') }; }
      if (!response.ok) {
        if (response.status === 409) { await load(month); return { ok: false, error: t('error.configUpdated') }; }
        return { ok: false, error: response.status === 403 ? t('error.httpsRequired') : t('error.saveFailed') };
      }
      await load(month); return { ok: true };
    } catch { return { ok: false, error: t('error.connectionInterrupted') }; }
  }

  async function dismissAlert(alert) {
    const sessionToken = token.current;
    try {
      const response = await fetch(`/admin/api/alerts/${encodeURIComponent(alert.id)}/dismiss`, {
        method: 'POST', headers: { Authorization: `Bearer ${sessionToken}`, 'Content-Type': 'application/json', 'X-Oracle-Action': 'manage' },
        credentials: 'omit', cache: 'no-store', body: JSON.stringify({ version: alert.version }),
      });
      if (token.current !== sessionToken) return { ok: false, error: t('login.stateChanged') };
      if (response.status === 401) { logout(); return { ok: false, error: t('login.relogin') }; }
      if (!response.ok) { await load(month); return { ok: false, error: t('error.alertUpdated') }; }
      await load(month); return { ok: true };
    } catch { return { ok: false, error: t('error.connectionRefresh') }; }
  }

  const title = t(`page.title.${tab}`);
  const strategy = data.scheduling?.strategy;
  const strategyLabel = strategy ? t(`scheduling.strategy.${strategy}`) : '';
  const strategyExplanation = data.scheduling?.explanation_key
    ? t(data.scheduling.explanation_key)
    : data.scheduling?.explanation;

  return <div className={styles.workspace}>
    <aside className={styles.sidebar}>
      <a className={styles.brand} href="/admin/">
        <img className={styles.brandIcon} src={isDark ? iconObsidian32 : icon32} alt="" />
        <span className={styles.brandText}>MagicalGirl<br />WebOracle</span>
      </a>
      <TabList className={styles.nav} vertical={!mobile} selectedValue={tab} onTabSelect={(_, event) => setTab(event.value)} aria-label={t('nav.aria')}>
        <Tab value="overview" icon={mobile ? undefined : <Apps20Regular />}>{t('nav.overview')}</Tab>
        <Tab value="logs" icon={mobile ? undefined : <List20Regular />}>{t('nav.logs')}</Tab>
        <Tab value="history" icon={mobile ? undefined : <ArrowClockwise20Regular />}>{t('nav.history')}</Tab>
        <Tab value="manage" icon={mobile ? undefined : <Key20Regular />}>{t('nav.manage')}</Tab>
        <Tab value="alerts" icon={mobile ? undefined : <Warning20Regular />}>
          {t('nav.alerts')}
          {pendingAlerts.length ? <CounterBadge count={pendingAlerts.length} size="small" color="danger" /> : null}
        </Tab>
      </TabList>
      <Caption1 className={styles.note}>{t('sidebar.quota')}<br />{t('sidebar.usage')}</Caption1>
    </aside>

    <main className={styles.main}>
      <header className={styles.header}>
        <div className={styles.titleBlock}>
          <Title1 as="h1" id="page-title">{title}</Title1>
          <Text className={styles.description}>{t('page.description')}</Text>
        </div>
        <div className={styles.actions}>
          <LanguageSwitcher />
          <ThemeMenu />
          <Toolbar size="small" aria-label={t('page.actions')}>
            <ToolbarGroup>
              <Button appearance="primary" disabled={syncing || data.mode === 'legacy' || !accounts.length} onClick={syncBalances}>
                {syncing ? t('action.syncing') : t('action.sync')}
              </Button>
              <ToolbarButton icon={<ArrowClockwise20Regular />} id="refresh" disabled={loading} onClick={() => void load(month)}>{t('common.refresh')}</ToolbarButton>
              <ToolbarDivider />
              <ToolbarButton icon={<SignOut20Regular />} onClick={logout}>{t('common.logout')}</ToolbarButton>
            </ToolbarGroup>
          </Toolbar>
        </div>
      </header>

      {notice ? <MessageBar intent="info" className="quota-notice">
        <MessageBarBody>{t('notice.budget')}</MessageBarBody>
        <MessageBarActions containerAction={<Button appearance="transparent" icon={<Dismiss20Regular />} aria-label={t('action.closeNotice')} onClick={() => setNotice(false)} />} />
      </MessageBar> : null}
      {syncMessage ? <MessageBar intent="info"><MessageBarBody>{syncMessage}</MessageBarBody></MessageBar> : null}
      {error ? <MessageBar intent="error" id="load-error"><MessageBarBody>{t('error.loadFailed', { error })}</MessageBarBody></MessageBar> : null}
      {tab !== 'alerts' && pendingAlerts.length ? <MessageBar intent="warning" className="alert-summary">
        <MessageBarBody>{t('alert.summary', { count: pendingAlerts.length })}</MessageBarBody>
        <MessageBarActions><Button appearance="transparent" onClick={() => setTab('alerts')}>{t('action.viewAlerts')}</Button></MessageBarActions>
      </MessageBar> : null}

      <section className={styles.summary} aria-label={t('nav.overview')}>
        {stats.map(item => <Card key={item.id} appearance="filled-alternative" className={styles.statCard}>
          <CardHeader
            header={<Text weight="semibold">{item.label}</Text>}
            action={<span className={styles.statIcon}>{item.icon}</span>}
          />
          <Text size={800} weight="semibold" id={item.id}>{item.value}</Text>
        </Card>)}
      </section>

      <Card appearance="outline" className={`${styles.panel} panel-card`}>
        <CardHeader
          header={<Title2 as="h2">{t(`panel.title.${tab}`)}</Title2>}
          action={<div className={`${styles.panelFilters} panel-filters`}>
            <Field label={t('filter.provider')} orientation="horizontal">
              <Select id="provider" value={provider} onChange={(_, event) => setProvider(event.value)}>
                <option value="">{t('filter.allProviders')}</option>
                {providers.map(value => <option key={value} value={value}>{value}</option>)}
              </Select>
            </Field>
            {tab !== 'manage' && tab !== 'alerts' ? <Field label={t('filter.month')} orientation="horizontal">
              <Select className="month-select" id="month" value={month} onChange={(_, event) => setMonth(event.value)}>
                {monthOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
              </Select>
            </Field> : null}
          </div>}
        />
        <Divider />
        {loading ? <Spinner size="tiny" label={t('spinner.refreshing')} className="refresh-spinner" /> : null}
        {tab === 'overview' ? <div id="overview-view"><QuotaTable accounts={accounts} keys={keys} pools={data.pools} />
          <div className="table-note"><Text size={200}>{t('note.poolShared')}</Text><Text size={100} className="muted" block>{t('note.pendingReserved')}</Text>
            {data.mode === 'legacy' ? <Text block size={200}>{t('note.legacyMode')}</Text> : null}
          </div></div> : tab === 'history' ? <BalanceHistory history={data.balance_history || []} accounts={accounts} /> : tab === 'manage' ? <KeyManagement data={data} accounts={accounts} keys={keys} onMutation={mutate} /> : tab === 'alerts' ? <Alerts alerts={alerts} history={(data.alert_history || []).filter(event => relevantIds.has(event.scope_id))} onDismiss={dismissAlert} /> : <CallTable calls={calls} traces={traces} onReadTrace={readTrace} />}
      </Card>

      <Caption1 className={styles.updated}>{t('note.updatedAt', { time: formatDateTime(data.generated_at), month: data.month })}</Caption1>
      {data.scheduler ? <Caption1 className={styles.updated}>
        {!data.scheduler.enabled ? t('scheduler.disabled') : data.scheduler.external ? t('scheduler.external', { seconds: data.scheduler.interval_seconds }) : data.scheduler.running ? t('scheduler.running', { seconds: data.scheduler.interval_seconds }) : t('scheduler.notStarted')}
        {' · '}{t('scheduler.refreshNote')}{data.scheduler.last_error ? ` · ${t('scheduler.lastError')}` : ''}
      </Caption1> : null}
      {data.scheduling ? <Caption1 className={styles.updated}>
        {t('scheduling.prefix', { strategy: strategyLabel })}
        {data.scheduling.preferred_account ? ` · ${t('scheduling.preferredAccount', { account: data.scheduling.preferred_account })}` : ''}
        {' · '}{strategyExplanation}
      </Caption1> : null}
      {data.cache ? <Caption1 className={styles.updated}>
        {data.cache.enabled ? t('cache.enabled', {
          entries: data.cache.entries ?? 0, hits: data.cache.hits ?? 0,
          searchTtl: data.cache.search_ttl_seconds ?? 0, fetchTtl: data.cache.fetch_ttl_seconds ?? 0,
        }) : t('cache.disabled')}
        {' · '}{t('cache.freshnessNote')}
      </Caption1> : null}
    </main>
  </div>;
}
