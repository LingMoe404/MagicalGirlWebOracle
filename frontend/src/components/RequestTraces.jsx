import React, { useEffect, useRef, useState } from 'react';
import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  MessageBar, MessageBarBody, Spinner, Subtitle2, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text,
} from '@fluentui/react-components';
import { useI18n } from '../i18n.jsx';

export default function RequestTraces({ traces, onRead }) {
  const { t, formatNumber, formatDateTime, formatList } = useI18n();
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const sequence = useRef(0);
  useEffect(() => () => { sequence.current += 1; }, []);

  const label = (prefix, code) => {
    if (code == null) return '';
    const key = `${prefix}.${code}`;
    const value = t(key);
    return value === key ? code : value;
  };
  const headers = ['trace.header.time', 'trace.header.provider', 'trace.header.result', 'trace.header.detail'];

  function close() { sequence.current += 1; setOpen(false); setDetail(null); setLoading(false); }
  async function show(id) {
    const request = ++sequence.current;
    setOpen(true); setLoading(true); setDetail(null); setError('');
    const result = await onRead(id);
    if (request !== sequence.current) return;
    setLoading(false);
    if (result.ok) setDetail(result.data); else setError(result.error);
  }
  return <section className="request-traces">
    <Subtitle2 as="h3">{t('trace.heading')}</Subtitle2>
    <Text size={200} className="muted" block>{t('trace.description')}</Text>
    <div className="table-scroll"><Table aria-label={t('trace.aria')}>
      <TableHeader><TableRow>{headers.map(key => <TableHeaderCell key={key}>{t(key)}</TableHeaderCell>)}</TableRow></TableHeader>
      <TableBody>{traces.map(trace => <TableRow key={trace.id}>
        <TableCell>{formatDateTime(trace.ts)}<Text size={200} block>{trace.operation}</Text></TableCell>
        <TableCell>{formatList(trace.providers) || t('trace.noProvider')}</TableCell>
        <TableCell>{label('trace.status', trace.status)}<Text size={200} block>{trace.latency_ms} ms</Text></TableCell>
        <TableCell><Button size="small" onClick={() => show(trace.id)} aria-label={t('trace.viewAria', { id: trace.id })}>{t('trace.view')}</Button></TableCell>
      </TableRow>)}</TableBody>
    </Table></div>
    {!traces.length ? <div className="empty">{t('trace.empty')}</div> : null}
    <Dialog open={open} onOpenChange={(_, data) => { if (!data.open) close(); }}>
      <DialogSurface className="trace-dialog"><DialogBody><DialogTitle>{t('trace.dialogTitle')}</DialogTitle><DialogContent>
        {loading ? <Spinner label={t('trace.loading')} /> : null}
        {error ? <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar> : null}
        {detail ? <>
          <Text block className="trace-id">{detail.id}</Text><Text block>{detail.operation} · {label('trace.status', detail.status)} · {detail.latency_ms} ms</Text>
          {detail.dropped_events ? <MessageBar intent="warning"><MessageBarBody>{t('trace.dropped', { count: detail.dropped_events })}</MessageBarBody></MessageBar> : null}
          <ol className="trace-timeline">{detail.events.map(event => <li key={event.id}>
            <Text weight="semibold" block>{label('trace.event', event.event)} · {event.details.provider || event.details.operation}</Text>
            <Text size={200} block>{event.details.key_id ? `${t('trace.key', { key: event.details.key_id })} · ` : ''}{label('trace.reason', event.details.reason)}{event.details.rank ? ` · ${t('trace.rank', { rank: event.details.rank })}` : ''}</Text>
            {event.details.estimate_units != null ? <Text size={200} block>{t('trace.eventReserved', { value: formatNumber(event.details.estimate_units / 1e6), unit: event.details.unit })}</Text> : null}
            <Text size={100} className="muted" block>{formatDateTime(event.ts)}{event.details.latency_ms != null ? ` · ${event.details.latency_ms} ms` : ''}</Text>
          </li>)}</ol>
          <Subtitle2 as="h3">{t('trace.budgetHeading')}</Subtitle2>
          {!detail.calls.length ? <Text block>{t('trace.noReservation')}</Text> : detail.calls.map(call => {
            const settledEvent = detail.events.find(event => event.event === 'settled' && event.details.call_id === call.id);
            const source = settledEvent?.details.cost_source;
            return <div className="trace-cost" key={call.id}>
              <Text weight="semibold" block>{call.provider} / {call.key_id} · {label('trace.state', call.state)}</Text>
              <Text size={100} className="trace-id muted" block>{t('trace.callId', { id: call.id })}</Text>
              <Text size={200} block>{t('trace.costSource', { source: source ? label('trace.costSource', source) : t('trace.notSettled') })}</Text>
              {call.allocations.map(allocation => {
                const stateText = call.state === 'settled'
                  ? t('trace.settled', { value: formatNumber(allocation.charged / 1e6) })
                  : call.state === 'released'
                    ? t('trace.released')
                    : t('trace.pendingSettlement');
                return <Text key={allocation.pool_id} size={200} block>{t('trace.pool', {
                  id: allocation.pool_id,
                  reserved: formatNumber(allocation.reserved / 1e6),
                  state: stateText,
                  unit: allocation.unit,
                })}</Text>;
              })}
            </div>;
          })}
          <Text size={200} className="muted" block>{t('trace.completionNote')}</Text>
        </> : null}
      </DialogContent><DialogActions><Button appearance="primary" onClick={close}>{t('trace.close')}</Button></DialogActions></DialogBody></DialogSurface>
    </Dialog>
  </section>;
}
