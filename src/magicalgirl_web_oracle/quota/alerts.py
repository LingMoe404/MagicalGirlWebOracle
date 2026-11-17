"""Persistent, transition-based station alerts. Never sends external messages."""
from __future__ import annotations

from decimal import Decimal
import time
from .ledger import uses_total_ceiling


class AlertEngine:
    def __init__(self,executor,*,low_ratio=0.1):
        self.executor=executor
        self.low_ratio=Decimal(str(low_ratio))

    def evaluate(self,*,now=None):
        moment=time.time() if now is None else now
        executor=self.executor
        executor.manager.reload()
        states=executor.ledger.key_states()
        balances=executor.ledger.balances()
        alerts=[]

        def add(scope,identity,kind,severity,title,message,**details):
            details={'title':title,'message':message,
                     'title_key':f'alert.{kind}.title',
                     'message_key':f'alert.{kind}.message',
                     **details}
            alerts.append(dict(id=f'{scope}:{identity}:{kind}',scope_kind=scope,scope_id=identity,kind=kind,severity=severity,
                               details=details))

        for account in executor.config.accounts:
            balance=balances.get(account.id,{})
            if account.provider=='you' and any(pool.account_id==account.id and pool.unit!='usd' for pool in executor.config.pools):
                add('account',account.id,'unsupported_unit','warning','You.com 预算单位需要核对',f'{account.label} 的带 Key 搜索使用 USD 预算；旧 requests 配置保持阻断，不自动换算或清零。',name=account.label)
            if account.provider=='jina' and any(key.account_id==account.id and key.enabled for key in executor.config.keys):
                add('account',account.id,'unsupported_cost','info','Jina 重排尚未开放',f'{account.label} 已支持有界 Reader；重排成本上界仍待核实。',name=account.label)
                if sum(key.account_id==account.id for key in executor.config.keys)!=1:
                    add('account',account.id,'wallet_mismatch','warning','Jina 钱包归属需调整',f'{account.label} 含多个 Key；请按 Key 分别建立独立钱包，不共享未核实的预算。',name=account.label)
            if balance.get('observed_at'):
                if balance.get('expires_at',0)<=moment:
                    add('account',account.id,'stale_balance','error','官方余额快照已过期',f'{account.label} 的受限预算已暂停，等待重新同步。',name=account.label)
                elif not balance.get('budget_verified') and not uses_total_ceiling(balance):
                    add('account',account.id,'unverified_balance','warning','官方余额的免费来源待核实',f'{account.label} 的总余额不能视为可用免费额度。',name=account.label)
            if balance.get('failures',0)>=3:
                add('account',account.id,'sync_failure','warning','官方余额连续同步失败',f'{account.label} 已连续失败 {balance["failures"]} 次，保留上次快照。',name=account.label,failures=balance['failures'])

        for key in executor.config.keys:
            if not key.enabled:
                continue
            state=states.get(key.id,{})
            if not executor._secrets.get(key.id):
                add('key',key.id,'missing_key','info','Key 尚未配置凭据',f'{key.label} 当前不参与调用。',name=key.label)
            elif state.get('disabled') and state.get('reason')=='auth':
                add('key',key.id,'invalid_key','error','Key 验证失败，已停用',f'请检查或替换 {key.label} 的凭据。',name=key.label)
            elif state.get('cooldown',0)>moment and state.get('reason')=='rate_limit':
                add('key',key.id,'rate_limit','warning','Key 正在限流冷却',f'{key.label} 冷却后将自动重新参与调度。',name=key.label)

        active_accounts={key.account_id for key in executor.config.keys if key.enabled}
        for pool in executor.config.pools:
            if pool.account_id not in active_accounts:
                continue
            account=executor.accounts[pool.account_id]
            if not pool.free_allowance_confirmed or pool.initial_used is None or pool.window.kind=='unknown':
                add('pool',pool.id,'unconfirmed_pool','info','账户免费预算尚未确认',f'请核对 {account.label} 的免费总量、期初用量与账期。',name=account.label)
                continue
            balance=balances.get(pool.account_id,{})
            if balance.get('observed_at') and (balance.get('expires_at',0)<=moment or (not balance.get('budget_verified') and not uses_total_ceiling(balance))):
                continue  # account-level alerts explain the pause; do not call it depletion
            remaining=executor.ledger.remaining(pool,moment)
            if remaining is None or pool.limit is None:
                continue
            if remaining==0:
                add('pool',pool.id,'budget_unavailable','error','账户可用免费预算不足',f'{account.label} 的预算不足或被保守预留，请检查真实余额与未决调用。',name=account.label,remaining='0',unit=pool.unit)
            elif self.low_ratio>0 and pool.limit>0 and remaining/pool.limit<=self.low_ratio:
                add('pool',pool.id,'low_balance','warning','账户免费预算即将用尽',f'{account.label} 剩余 {remaining} {pool.unit}。',name=account.label,remaining=str(remaining),unit=pool.unit,threshold=str(self.low_ratio))
            else:
                forecast=executor.ledger.forecast(pool,now=moment)
                if forecast and forecast['estimated_days']<=2 and forecast['before_reset'] is not False:
                    add('pool',pool.id,'forecast_depletion','warning','按近期速度，预算可能提前耗尽',f'{account.label} 依据近 7 天本地已结算用量，预计约 {forecast["estimated_days"]} 天；仅为估算。',name=account.label,estimated_days=forecast['estimated_days'])
        executor.ledger.update_alerts(alerts,now=moment)
        return executor.ledger.active_alerts()
