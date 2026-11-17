import React, { Fragment } from 'react';
import {
  Accordion, AccordionHeader, AccordionItem, AccordionPanel,
  Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text,
} from '@fluentui/react-components';
import { Key20Regular, Stack20Regular } from '@fluentui/react-icons';
import { KeyStatus, Remaining } from './common.jsx';
import { useI18n } from '../i18n.jsx';

function KeyRows({ account, keys, pools }) {
  const { t, formatNumber } = useI18n();
  const accountKeys = keys.filter(key => key.account_id === account.id);
  const poolIds = new Set(accountKeys.flatMap(key => key.pool_ids));
  const accountPools = pools.filter(pool => poolIds.has(pool.id));
  const status = accountKeys.some(key => key.status === 'ready') ? 'ready' : accountKeys[0]?.status || 'unconfirmed';
  return <Fragment>
    <TableRow className="account-row">
      <TableCell><div className="account-name"><Stack20Regular /><Text weight="semibold">{t('quota.account', { label: account.label, count: accountKeys.length })}</Text></div></TableCell>
      <TableCell>{account.provider}</TableCell><TableCell>—</TableCell><TableCell>—</TableCell>
      <TableCell><Remaining pools={accountPools} /></TableCell><TableCell><KeyStatus status={status} /></TableCell>
    </TableRow>
    {accountKeys.map(key => <TableRow key={key.id}>
      <TableCell><div className="key-name"><Key20Regular /><div><Text>{key.label}</Text><Text size={100} block className="muted" title={key.id}>ID · {key.id.slice(-8)}</Text></div></div></TableCell>
      <TableCell>{account.provider}</TableCell><TableCell>{key.calls == null ? '—' : formatNumber(key.calls)}</TableCell>
      <TableCell>{key.consumed == null ? '—' : `${formatNumber(key.consumed)} ${key.unit}`}</TableCell>
      <TableCell><Remaining pools={pools.filter(pool => key.pool_ids.includes(pool.id))} shared /></TableCell>
      <TableCell><KeyStatus status={key.status} /></TableCell>
    </TableRow>)}
  </Fragment>;
}

export default function QuotaTable({ accounts, keys, pools }) {
  const { t, formatNumber } = useI18n();
  const headers = [
    'quota.header.account', 'quota.header.provider', 'quota.header.calls',
    'quota.header.consumed', 'quota.header.remaining', 'quota.header.status',
  ];
  return <>
    <div className="desktop-accounts table-scroll">
      <Table aria-label={t('quota.aria')} size="medium">
        <TableHeader><TableRow>{headers.map(key => <TableHeaderCell key={key}>{t(key)}</TableHeaderCell>)}</TableRow></TableHeader>
        <TableBody id="key-rows">{accounts.map(account => <KeyRows key={account.id} account={account} keys={keys} pools={pools} />)}</TableBody>
      </Table>
    </div>
    <Accordion collapsible multiple id="mobile-accounts">
      {accounts.map(account => {
        const accountKeys = keys.filter(key => key.account_id === account.id);
        const poolIds = new Set(accountKeys.flatMap(key => key.pool_ids));
        return <AccordionItem key={account.id} value={account.id}>
          <AccordionHeader><span className="mobile-account-title">{t('quota.account', { label: account.label, count: accountKeys.length })}<Text size={100} className="muted">{account.provider}</Text></span></AccordionHeader>
          <AccordionPanel>
            <div className="mobile-balance"><Text size={200}>{t('quota.sharedRemaining')}</Text><Remaining pools={pools.filter(pool => poolIds.has(pool.id))} /></div>
            {accountKeys.map(key => <div className="mobile-key" key={key.id}>
              <Text weight="semibold" block>{key.label}</Text><Text size={100} className="muted" block>{key.id}</Text>
              <div className="mobile-metrics"><Text size={200}>{t('quota.calls', { count: formatNumber(key.calls) })}</Text><Text size={200}>{t('quota.consumed', { value: formatNumber(key.consumed), unit: key.unit })}</Text></div>
              <KeyStatus status={key.status} />
            </div>)}
          </AccordionPanel>
        </AccordionItem>;
      })}
    </Accordion>
    {!accounts.length ? <div className="empty">{t('quota.empty')}</div> : null}
  </>;
}
