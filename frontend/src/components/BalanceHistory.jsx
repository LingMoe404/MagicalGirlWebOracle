import React from 'react';
import { Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text } from '@fluentui/react-components';
import { useI18n } from '../i18n.jsx';

export default function BalanceHistory({ history, accounts }) {
  const { t, formatNumber } = useI18n();
  const names = new Map(accounts.map(account => [account.id, account.label]));
  const headers = ['balance.header.time', 'balance.header.account', 'balance.header.provider', 'balance.header.total', 'balance.header.free', 'balance.header.basis'];
  return <>
    <div className="table-scroll"><Table aria-label={t('balance.aria')}>
      <TableHeader><TableRow>{headers.map(key => <TableHeaderCell key={key}>{t(key)}</TableHeaderCell>)}</TableRow></TableHeader>
      <TableBody>{history.filter(row => names.has(row.account_id)).map(row => <TableRow key={row.id}>
        <TableCell>{new Date(row.observed_at * 1000).toISOString().replace('T', ' ').slice(0, 19)}</TableCell>
        <TableCell>{names.get(row.account_id)}</TableCell><TableCell>{row.provider}</TableCell>
        <TableCell>{formatNumber(row.remaining)} {row.unit}</TableCell>
        <TableCell>{row.free_remaining == null ? t('balance.notSeparated') : `${formatNumber(row.free_remaining)} ${row.unit}`}</TableCell>
        <TableCell><Text className={row.budget_verified ? 'key-status ready' : 'key-status attention'}>{row.budget_verified ? t('balance.verified') : row.cap_policy === 'total_ceiling' ? t('balance.ceiling') : t('balance.unverified')}</Text></TableCell>
      </TableRow>)}</TableBody></Table></div>
    {!history.some(row => names.has(row.account_id)) ? <div className="empty">{t('balance.empty')}</div> : null}
    <Text size={200} className="log-note muted">{t('balance.note')}</Text>
  </>;
}
