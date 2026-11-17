import React, { useState } from 'react';
import {
  Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Input, MessageBar, MessageBarBody, Select, Table, TableBody, TableCell, TableHeader,
  TableHeaderCell, TableRow, Text,
} from '@fluentui/react-components';
import { Add20Regular, Edit20Regular } from '@fluentui/react-icons';
import { KeyStatus } from './common.jsx';
import { useI18n } from '../i18n.jsx';

function ManagementDialog({ mode, data, onClose, onMutation }) {
  const { t } = useI18n();
  const [label, setLabel] = useState(mode.key?.label || '');
  const [secret, setSecret] = useState('');
  const [account, setAccount] = useState(mode.account?.id || '_new');
  const [provider, setProvider] = useState('tavily');
  const [accountLabel, setAccountLabel] = useState('');
  const [owner, setOwner] = useState('');
  const [enabled, setEnabled] = useState(mode.key?.enabled ?? true);
  const [limit, setLimit] = useState('');
  const [used, setUsed] = useState('');
  const [windowKind, setWindowKind] = useState(() => ['you', 'jina'].includes(data.accounts.find(item => item.id === mode.pool?.account_id)?.provider) ? 'one_time' : 'monthly');
  const [day, setDay] = useState('1');
  const [zone, setZone] = useState('UTC');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const secure = window.isSecureContext;
  const title = t(`keymgmt.title.${mode.type}`);

  async function submit(event) {
    event.preventDefault(); setBusy(true); setError('');
    let route, method, body;
    if (mode.type === 'create') {
      route = '/admin/api/keys'; method = 'POST';
      body = { label, secret, ...(account === '_new' ? { provider, account_label: accountLabel, owner_label: owner } : { account_id: account }) };
    } else if (mode.type === 'pool') {
      route = `/admin/api/pools/${mode.pool.id}`; method = 'PATCH';
      body = { limit, initial_used: used, free_allowance_confirmed: confirmed,
        window: { kind: windowKind, timezone: zone, ...(windowKind === 'monthly' ? { reset_day: Number(day) } : {}) } };
    } else {
      route = `/admin/api/keys/${mode.key.id}`; method = 'PATCH';
      body = { label, enabled, ...(secret ? { secret } : {}) };
    }
    const result = await onMutation(route, method, { ...body, revision: mode.revision });
    setBusy(false);
    if (result.ok) { setSecret(''); onClose(); } else setError(result.error);
  }

  function close() { if (!busy) { setSecret(''); onClose(); } }
  const poolProvider = data.accounts.find(item => item.id === mode.pool?.account_id)?.provider;
  const keyProvider = mode.key ? data.accounts.find(item => item.id === mode.key.account_id)?.provider : null;
  const jinaHint = (provider === 'jina' && account === '_new') || keyProvider === 'jina';
  return <Dialog open onOpenChange={(_, event) => { if (!event.open) close(); }}>
    <DialogSurface><form onSubmit={submit}><DialogBody>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent><div className="manage-fields">
        {mode.type === 'pool' ? <>
          <Text>{t('keymgmt.pool.intro')}</Text>
          {poolProvider === 'you' ? <Text size={200}>{t('keymgmt.pool.youNote')}</Text> : null}
          <Field label={t('keymgmt.pool.limit', { unit: mode.pool.unit })} required><Input type="number" min="0" step="any" value={limit} onChange={(_, event) => setLimit(event.value)} required /></Field>
          <Field label={t('keymgmt.pool.used')} required><Input type="number" min="0" step="any" value={used} onChange={(_, event) => setUsed(event.value)} required /></Field>
          <Field label={t('keymgmt.pool.window')}><Select value={windowKind} onChange={(_, event) => setWindowKind(event.value)}>
            <option value="monthly">{t('keymgmt.pool.monthly')}</option>
            <option value="daily">{t('keymgmt.pool.daily')}</option>
            <option value="one_time">{t('keymgmt.pool.oneTime')}</option>
          </Select></Field>
          {windowKind === 'monthly' ? <Field label={t('keymgmt.pool.resetDay')} required><Input type="number" min="1" max="31" value={day} onChange={(_, event) => setDay(event.value)} required /></Field> : null}
          <Field label={t('keymgmt.pool.timezone')} required><Input value={zone} onChange={(_, event) => setZone(event.value)} required /></Field>
          <Checkbox label={t('keymgmt.pool.confirmed')} checked={confirmed} onChange={(_, event) => setConfirmed(event.checked === true)} />
        </> : <>
          {mode.type === 'create' ? <>
            <Field label={t('keymgmt.account.label')}><Select value={account} onChange={(_, event) => setAccount(event.value)}>
              <option value="_new">{t('keymgmt.account.new')}</option>
              {data.accounts.filter(item => item.provider !== 'jina').map(item => <option key={item.id} value={item.id}>{item.label} · {item.provider}</option>)}
            </Select></Field>
            {account === '_new' ? <>
              <Field label={t('keymgmt.account.provider')}><Select value={provider} onChange={(_, event) => setProvider(event.value)}>
                <option value="tavily">{t('keymgmt.provider.tavily')}</option>
                <option value="exa">{t('keymgmt.provider.exa')}</option>
                <option value="firecrawl">{t('keymgmt.provider.firecrawl')}</option>
                <option value="jina">{t('keymgmt.provider.jina')}</option>
                <option value="you">{t('keymgmt.provider.you')}</option>
              </Select></Field>
              <Field label={t('keymgmt.accountName')} required><Input value={accountLabel} onChange={(_, event) => setAccountLabel(event.value)} required maxLength={100} placeholder={t('keymgmt.accountNamePlaceholder')} /></Field>
              <Field label={t('keymgmt.ownerLabel')}><Input value={owner} onChange={(_, event) => setOwner(event.value)} maxLength={100} placeholder={t('keymgmt.ownerPlaceholder')} /></Field>
            </> : <Text size={200}>{t('keymgmt.sharedPool')}</Text>}
          </> : null}
          {jinaHint ? <Text size={200}>{t('keymgmt.jinaNote')}</Text> : null}
          <Field label={t('keymgmt.keyName')} required><Input value={label} onChange={(_, event) => setLabel(event.value)} required maxLength={100} /></Field>
          <Field label={mode.type === 'create' ? t('keymgmt.apiKey') : t('keymgmt.replaceKey')} required={mode.type === 'create'}>
            <Input type="password" autoComplete="off" value={secret} onChange={(_, event) => setSecret(event.value)} required={mode.type === 'create'} maxLength={4096} disabled={!secure} />
          </Field>
          {!secure ? <MessageBar intent="warning"><MessageBarBody>{t('keymgmt.secureWarning')}</MessageBarBody></MessageBar> : null}
          {mode.type === 'edit' ? <Checkbox label={t('keymgmt.enable')} checked={enabled} onChange={(_, event) => setEnabled(event.checked === true)} /> : <Text size={200}>{t('keymgmt.newAccountNote')}</Text>}
          <Text size={200} className="muted">{t('keymgmt.encryptedNote')}</Text>
        </>}
        {error ? <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar> : null}
      </div></DialogContent>
      <DialogActions>
        <Button onClick={close} disabled={busy} type="button">{t('common.cancel')}</Button>
        <Button appearance="primary" type="submit" disabled={busy || (mode.type === 'pool' && !confirmed) || (mode.type === 'create' && !secure)}>{busy ? t('common.saving') : t('common.save')}</Button>
      </DialogActions>
    </DialogBody></form></DialogSurface>
  </Dialog>;
}

export default function KeyManagement({ data, accounts, keys, onMutation }) {
  const { t } = useI18n();
  const [mode, setMode] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (!data.management_enabled) return <MessageBar intent="info"><MessageBarBody>{t('keymgmt.disabled')}</MessageBarBody></MessageBar>;
  async function toggle(key) {
    setBusy(true); setError('');
    const result = await onMutation(`/admin/api/keys/${key.id}`, 'PATCH', { enabled: !key.enabled });
    setBusy(false); if (!result.ok) setError(result.error);
  }
  return <div className="key-management">
    <div className="management-toolbar">
      <Button appearance="primary" icon={<Add20Regular />} onClick={() => setMode({ type: 'create', revision: data.revision })}>{t('keymgmt.add')}</Button>
      <Text size={200} className="muted">{t('keymgmt.revision', { revision: data.revision })}</Text>
    </div>
    {error ? <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar> : null}
    {accounts.map(account => <section className="managed-account" key={account.id}>
      <div className="managed-account-heading"><Text weight="semibold">{account.label} · {account.provider}</Text>
        {data.pools.filter(pool => pool.account_id === account.id && pool.window === 'unknown').map(pool => <Button key={pool.id} onClick={() => setMode({ type: 'pool', pool, revision: data.revision })}>{t('keymgmt.confirmAllowance')}</Button>)}
      </div>
      <div className="table-scroll management-table"><Table aria-label={t('keymgmt.tableAria', { account: account.label })}>
        <TableHeader><TableRow>
          <TableHeaderCell>{t('keymgmt.header.keyName')}</TableHeaderCell>
          <TableHeaderCell>{t('keymgmt.header.status')}</TableHeaderCell>
          <TableHeaderCell>{t('keymgmt.header.actions')}</TableHeaderCell>
        </TableRow></TableHeader>
        <TableBody>{keys.filter(key => key.account_id === account.id).map(key => <TableRow key={key.id}>
          <TableCell><Text block>{key.label}</Text><Text size={100} block className="muted" title={key.id}>ID · {key.id.slice(-8)}</Text></TableCell>
          <TableCell><KeyStatus status={key.status} /></TableCell>
          <TableCell><div className="key-actions">
            <Button icon={<Edit20Regular />} onClick={() => setMode({ type: 'edit', key, revision: data.revision })}>{t('keymgmt.edit')}</Button>
            <Button disabled={busy} onClick={() => toggle(key)}>{key.enabled ? t('keymgmt.disable') : t('keymgmt.enableAction')}</Button>
          </div></TableCell>
        </TableRow>)}</TableBody>
      </Table></div>
    </section>)}
    {!accounts.length ? <div className="empty">{t('keymgmt.empty')}</div> : null}
    {mode ? <ManagementDialog mode={mode} data={data} onClose={() => setMode(null)} onMutation={onMutation} /> : null}
  </div>;
}
