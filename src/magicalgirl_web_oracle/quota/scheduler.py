"""HTTP/stdio lifecycle-owned balance polling with durable per-account backoff."""
from __future__ import annotations

import asyncio
import logging
import time

log=logging.getLogger(__name__)


class BalanceScheduler:
    def __init__(self,executor,*,interval=300,poll_interval=15):
        self.executor=executor
        self.interval=interval
        self.poll_interval=poll_interval
        self.stop_event=asyncio.Event()
        self.status={'enabled':interval>0,'running':False,'interval_seconds':interval,'last_tick':None,'last_error':None}
        executor.scheduler_status=self.status

    def due_accounts(self,now):
        states=self.executor.ledger.balances()
        keys=self.executor.ledger.key_states()
        due=[]
        for account in self.executor.config.accounts:
            if account.provider not in self.executor.balance_sync.factories:
                continue
            if account.provider=="you" and any(pool.account_id==account.id and pool.unit!="usd" for pool in self.executor.config.pools):
                continue
            if account.provider=="jina" and sum(key.account_id==account.id for key in self.executor.config.keys)!=1:
                continue
            eligible=any(key.account_id==account.id and key.enabled and self.executor._secrets.get(key.id) and not keys.get(key.id,{}).get('disabled') for key in self.executor.config.keys)
            if not eligible:
                continue
            state=states.get(account.id,{})
            if state.get('next_allowed',0)>now:
                continue
            if state.get('status') in ('failed','cancelled'):
                due.append(account.id)
                continue
            next_regular=state.get('last_attempt',0)+self.interval
            # A previously failed process can leave a syncing lease. Once its
            # cooldown expires, it is safe to claim the account again.
            if state.get('status')=='syncing' or next_regular<=now:
                due.append(account.id)
        return due

    async def tick(self):
        self.executor.manager.reload()
        now=time.time()
        results=[]
        if self.interval>0:
            due=self.due_accounts(now)
            for offset in range(0,len(due),50):
                results.extend(await self.executor.balance_sync.run(due[offset:offset+50]))
        self.executor.alerts.evaluate()
        self.status.update(last_tick=time.time(),last_error=None)
        return results

    async def run(self):
        self.status['running']=True
        try:
            while not self.stop_event.is_set():
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if self.status['last_error'] is None:
                        log.warning('background quota refresh failed; will retry without resetting budget')
                    self.status['last_error']='internal_refresh_error'
                try:
                    await asyncio.wait_for(self.stop_event.wait(),timeout=self.poll_interval)
                except TimeoutError:
                    pass
        finally:
            self.status['running']=False

    def stop(self):
        self.stop_event.set()
