import React from 'react';
import { Badge, ProgressBar, Text } from '@fluentui/react-components';
import { useI18n } from '../i18n.jsx';

const STATUS_COLORS = {
  ready: 'success',
  disabled: 'subtle',
  missing: 'warning',
  unsupported: 'warning',
  unsupported_unit: 'warning',
  wallet_mismatch: 'warning',
  cooldown: 'informative',
  unconfirmed: 'warning',
  exhausted: 'danger',
  legacy: 'subtle',
  stale_balance: 'danger',
  unverified_balance: 'warning',
};

export function KeyStatus({ status }) {
  const { t } = useI18n();
  const key = `status.${status}`;
  const label = t(key);
  return <Badge appearance="tint" color={STATUS_COLORS[status] || 'informative'} size="small">
    {label === key ? status : label}
  </Badge>;
}

export function Remaining({ pools, shared = false }) {
  const { t, formatNumber, formatTime } = useI18n();
  if (!pools.length) return <Text className="muted">{t('common.notAvailable')}</Text>;
  return pools.map(pool => <div className="pool-balance" key={pool.id}>
    <Text weight={shared ? 'regular' : 'semibold'}>
      {pool.remaining == null
        ? t('remaining.unconfirmed')
        : shared
          ? t('remaining.shared', { value: `${formatNumber(pool.remaining)} ${pool.unit}` })
          : `${formatNumber(pool.remaining)} ${pool.unit}`}
    </Text>
    {!shared && pool.remaining != null && Number(pool.limit) > 0
      ? <ProgressBar value={Math.max(0, Math.min(1, Number(pool.remaining) / Number(pool.limit)))} aria-label={t('remaining.aria', { id: pool.id })} />
      : null}
    {!shared ? <Text size={100} className="muted">{pool.source === 'estimated' ? t('remaining.localEstimate') : t('remaining.unknown')}</Text> : null}
    {!shared && pool.official_remaining != null ? <>
      <Text size={100}>{t(pool.official_total_ceiling ? 'remaining.officialTotal' : 'remaining.official', {
        value: formatNumber(pool.official_remaining), unit: pool.unit,
      })}</Text>
      <Text size={100} className={pool.official_stale || pool.sync_status === 'failed' ? 'attention' : 'muted'}>
        {pool.official_stale
          ? t('remaining.snapshotExpired')
          : pool.official_total_ceiling
            ? (pool.sync_status === 'failed' ? t('remaining.syncFailedCeiling') : t('remaining.totalCeiling'))
            : !pool.official_budget_verified
              ? t('remaining.poolBudgetPaused')
              : pool.sync_status === 'failed'
                ? t('remaining.syncFailed')
                : t('remaining.snapshotAt', { time: formatTime(pool.official_observed_at) })}
      </Text>
    </> : null}
    {!shared && pool.forecast ? <Text size={100} className="muted">
      {t('remaining.forecast', { days: formatNumber(pool.forecast.estimated_days) })}
      {pool.forecast.before_reset === false ? t('remaining.beforeReset') : ''}
    </Text> : null}
  </div>);
}
