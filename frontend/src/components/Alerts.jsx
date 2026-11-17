import React, { useState } from 'react';
import {
  Button, Checkbox, MessageBar, MessageBarBody, Subtitle2, Table, TableBody, TableCell,
  TableHeader, TableHeaderCell, TableRow, Text,
} from '@fluentui/react-components';
import { useI18n } from '../i18n.jsx';

export default function Alerts({ alerts, history, onDismiss }) {
  const { t, formatDateTime } = useI18n();
  const [showDismissed, setShowDismissed] = useState(false);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState('');
  const visible = alerts.filter(alert => showDismissed || alert.dismissed_at == null);

  const label = (prefix, code) => {
    const key = `${prefix}.${code}`;
    const value = t(key);
    return value === key ? code : value;
  };
  const alertText = (alert, field) => {
    const key = alert.details?.[`${field}_key`];
    return key ? t(key, alert.details) : alert.details?.[field];
  };

  async function dismiss(alert) {
    setBusy(alert.id); setError('');
    const result = await onDismiss(alert);
    setBusy(null); if (!result.ok) setError(result.error);
  }
  return <div className="alerts-panel">
    <Checkbox label={t('alert.showDismissed')} checked={showDismissed} onChange={(_, event) => setShowDismissed(event.checked === true)} />
    {error ? <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar> : null}
    {!visible.length ? <div className="empty">{t('alert.empty')}</div> : visible.map(alert => {
      const title = alertText(alert, 'title');
      return <section className="alert-item" key={alert.id}>
        <MessageBar intent={alert.severity === 'error' ? 'error' : alert.severity === 'warning' ? 'warning' : 'info'}>
          <MessageBarBody><Text weight="semibold" block>{title}</Text><Text block>{alertText(alert, 'message')}</Text>
            <Text size={100} className="muted" block>{t('alert.firstSeen', {
              time: formatDateTime(alert.first_seen), severity: label('alert.severity', alert.severity),
            })}{alert.dismissed_at != null ? ` · ${t('alert.dismissed')}` : ''}</Text>
          </MessageBarBody>
          {alert.dismissed_at == null ? <Button appearance="transparent" disabled={busy != null} onClick={() => dismiss(alert)} aria-label={t('alert.dismissAria', { title })}>{busy === alert.id ? t('common.saving') : t('alert.dismiss')}</Button> : null}
        </MessageBar>
      </section>;
    })}
    <Subtitle2 as="h3">{t('alert.history')}</Subtitle2>
    <div className="table-scroll"><Table aria-label={t('alert.historyAria')}>
      <TableHeader><TableRow>
        <TableHeaderCell>{t('alert.table.time')}</TableHeaderCell>
        <TableHeaderCell>{t('alert.table.change')}</TableHeaderCell>
        <TableHeaderCell>{t('alert.table.item')}</TableHeaderCell>
      </TableRow></TableHeader>
      <TableBody>{history.map(event => <TableRow key={event.id}>
        <TableCell>{formatDateTime(event.ts)}</TableCell>
        <TableCell>{label('alert.events', event.event)}</TableCell>
        <TableCell>{event.details?.title_key ? t(event.details.title_key, event.details) : event.details?.title}</TableCell>
      </TableRow>)}</TableBody>
    </Table></div>
    <Text size={200} className="log-note muted">{t('alert.note')}</Text>
  </div>;
}
