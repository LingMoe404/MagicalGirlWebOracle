import React from 'react';
import { Subtitle2, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text } from '@fluentui/react-components';
import RequestTraces from './RequestTraces.jsx';
import { useI18n } from '../i18n.jsx';

export default function CallTable({ calls, traces, onReadTrace }) {
  const { t, formatNumber } = useI18n();
  const stateLabel = state => {
    const key = `call.state.${state}`;
    const label = t(key);
    return label === key ? state : label;
  };
  const headers = ['call.header.time', 'call.header.key', 'call.header.provider', 'call.header.cost', 'call.header.status', 'call.header.latency'];
  return <>
    <RequestTraces traces={traces} onRead={onReadTrace} />
    <Subtitle2 as="h3">{t('call.heading')}</Subtitle2>
    <div className="table-scroll" id="logs-view"><Table aria-label={t('call.aria')}>
      <TableHeader><TableRow>{headers.map(key => <TableHeaderCell key={key}>{t(key)}</TableHeaderCell>)}</TableRow></TableHeader>
      <TableBody id="log-rows">{calls.map(call => <TableRow key={call.id}>
        <TableCell>{new Date(call.ts * 1000).toISOString().replace('T', ' ').slice(0, 19)}</TableCell>
        <TableCell>{call.key_id}</TableCell><TableCell>{call.provider} / {call.endpoint}</TableCell>
        <TableCell>{formatNumber((call.state === 'settled' ? call.charged_units : call.state === 'released' ? 0 : call.reserved_units) / 1e6)} {call.unit}</TableCell>
        <TableCell><Text className={call.state === 'settled' ? 'key-status ready' : 'key-status attention'}>{stateLabel(call.state)}</Text></TableCell>
        <TableCell>{call.latency_ms} ms</TableCell>
      </TableRow>)}</TableBody></Table></div>
    {!calls.length ? <div className="empty">{t('call.empty')}</div> : null}
    <Text size={200} className="log-note muted">{t('call.note')}</Text>
  </>;
}
