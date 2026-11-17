"""Atomic multi-key budget reservations; stores no credential material.

This is a separate, opt-in ledger. Existing usage.sqlite3 is never migrated or
reset implicitly. Opening consumption must be supplied explicitly per pool.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from uuid import uuid4

from .accounts import AccountConfig, ApiKeyRef, QuotaPool

SCALE = 1_000_000


def units(value) -> int:
    amount = Decimal(str(value))
    scaled = amount * SCALE
    if not amount.is_finite() or amount < 0 or scaled != scaled.to_integral_value() or scaled > 2**60:
        raise ValueError("invalid amount or unsupported precision (maximum 6 decimal places)")
    return int(scaled)


def uses_total_ceiling(snapshot):
    # This is a restriction on an independently confirmed local USD budget,
    # never evidence that a total balance is itself a free allowance.
    expected={'you':'usd','firecrawl':'credits'}.get(snapshot.get('provider'))
    return expected is not None and snapshot.get('unit')==expected and snapshot.get('cap_policy')=='total_ceiling'


class BudgetUnavailable(Exception):
    def __init__(self,message,*,reason='budget_or_key_unavailable'):
        super().__init__(message)
        self.reason=reason


class AccountLedger:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        try:
            existing = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='account_schema'").fetchone()
            version = self.db.execute("SELECT version FROM account_schema").fetchone() if existing else None
            if version and version[0] not in (1,2,3,4,5):
                raise ValueError("unsupported account ledger schema; database was not migrated")
            if version and version[0] in (1,2,3,4):
                backup_path = self.path.with_name(f"{self.path.stem}.before-v5-{time.time_ns()}.sqlite3")
                backup = sqlite3.connect(str(backup_path))
                try:
                    self.db.backup(backup)
                    if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("account ledger backup integrity check failed")
                finally:
                    backup.close()
            with self.transaction():
                self.db.execute("CREATE TABLE IF NOT EXISTS account_schema(version INTEGER NOT NULL)")
                row = self.db.execute("SELECT version FROM account_schema").fetchone()
                if row and row[0] not in (1,2,3,4,5):
                    raise ValueError("unsupported account ledger schema; database was not migrated")
                if not row:
                    self.db.execute("INSERT INTO account_schema VALUES(5)")
                for sql in (
                    "CREATE TABLE IF NOT EXISTS ledger_meta(name TEXT PRIMARY KEY, value TEXT NOT NULL)",
                    "CREATE TABLE IF NOT EXISTS pools(id TEXT PRIMARY KEY, identity TEXT NOT NULL, initial_used INTEGER, created REAL NOT NULL, blocked_until REAL NOT NULL DEFAULT 0)",
                    "CREATE TABLE IF NOT EXISTS credentials(id TEXT PRIMARY KEY, account_id TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE, disabled INTEGER NOT NULL DEFAULT 0, cooldown REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '')",
                    "CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, key_id TEXT NOT NULL, provider TEXT NOT NULL, endpoint TEXT NOT NULL, ts REAL NOT NULL, state TEXT NOT NULL, success INTEGER, error TEXT NOT NULL DEFAULT '', latency_ms INTEGER NOT NULL DEFAULT 0)",
                    "CREATE TABLE IF NOT EXISTS allocations(call_id TEXT NOT NULL REFERENCES calls(id), pool_id TEXT NOT NULL REFERENCES pools(id), reserved INTEGER NOT NULL, charged INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(call_id,pool_id))",
                    "CREATE INDEX IF NOT EXISTS calls_ts ON calls(ts)",
                    "CREATE INDEX IF NOT EXISTS allocations_pool ON allocations(pool_id)",
                    "CREATE TABLE IF NOT EXISTS balance_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,account_id TEXT NOT NULL,key_id TEXT NOT NULL,observed REAL NOT NULL,watermark INTEGER NOT NULL,held INTEGER NOT NULL,cap_units INTEGER NOT NULL,expires REAL NOT NULL,snapshot TEXT NOT NULL)",
                    "CREATE INDEX IF NOT EXISTS balance_account ON balance_snapshots(account_id,id)",
                    "CREATE TABLE IF NOT EXISTS balance_sync(account_id TEXT PRIMARY KEY,last_attempt REAL NOT NULL,next_allowed REAL NOT NULL,status TEXT NOT NULL,error TEXT NOT NULL DEFAULT '')",
                    "CREATE TABLE IF NOT EXISTS managed_config(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,base_hash TEXT NOT NULL,document TEXT NOT NULL)",
                    "CREATE TABLE IF NOT EXISTS managed_secrets(key_id TEXT PRIMARY KEY,ciphertext TEXT NOT NULL)",
                    "CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY,scope_kind TEXT NOT NULL,scope_id TEXT NOT NULL,kind TEXT NOT NULL,severity TEXT NOT NULL,active INTEGER NOT NULL,version INTEGER NOT NULL,first_seen REAL NOT NULL,last_seen REAL NOT NULL,resolved_at REAL,dismissed_at REAL,details TEXT NOT NULL)",
                    "CREATE TABLE IF NOT EXISTS request_traces(id TEXT PRIMARY KEY,operation TEXT NOT NULL,ts REAL NOT NULL,finished REAL,status TEXT NOT NULL,latency_ms INTEGER NOT NULL DEFAULT 0,dropped_events INTEGER NOT NULL DEFAULT 0)",
                    "CREATE INDEX IF NOT EXISTS traces_ts ON request_traces(ts)",
                    "CREATE TABLE IF NOT EXISTS trace_events(id INTEGER PRIMARY KEY AUTOINCREMENT,trace_id TEXT NOT NULL REFERENCES request_traces(id),ts REAL NOT NULL,event TEXT NOT NULL,details TEXT NOT NULL)",
                    "CREATE INDEX IF NOT EXISTS trace_events_request ON trace_events(trace_id,id)",
                    "CREATE TABLE IF NOT EXISTS alert_events(id INTEGER PRIMARY KEY AUTOINCREMENT,alert_id TEXT NOT NULL,ts REAL NOT NULL,event TEXT NOT NULL,severity TEXT NOT NULL,details TEXT NOT NULL)",
                ):
                    self.db.execute(sql)
                if 'failures' not in {r[1] for r in self.db.execute('PRAGMA table_info(balance_sync)')}:
                    self.db.execute('ALTER TABLE balance_sync ADD COLUMN failures INTEGER NOT NULL DEFAULT 0')
                self.db.execute("INSERT OR IGNORE INTO ledger_meta VALUES('fingerprint_salt',?)", (secrets.token_hex(32),))
                self.db.execute("UPDATE account_schema SET version=5")
            self.salt = self.db.execute("SELECT value FROM ledger_meta WHERE name='fingerprint_salt'").fetchone()[0]
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def transaction(self):
        with self._lock:
            nested=self.db.in_transaction
            savepoint="s_"+uuid4().hex
            self.db.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute(f"RELEASE {savepoint}" if nested else "COMMIT")
            except BaseException:
                if nested:
                    self.db.execute(f"ROLLBACK TO {savepoint}")
                    self.db.execute(f"RELEASE {savepoint}")
                else:
                    self.db.execute("ROLLBACK")
                raise

    def start_trace(self,operation):
        trace_id=uuid4().hex
        with self.transaction():
            self.db.execute("INSERT INTO request_traces(id,operation,ts,status) VALUES(?,?,?,'running')",(trace_id,operation,time.time()))
        return trace_id

    def finish_trace(self,trace_id,status,latency_ms):
        with self.transaction():
            self.db.execute("UPDATE request_traces SET status=?,finished=?,latency_ms=? WHERE id=? AND status='running'",(status,time.time(),latency_ms,trace_id))

    def append_trace(self,trace_id,event,details):
        with self.transaction():
            count=self.db.execute('SELECT COUNT(*) FROM trace_events WHERE trace_id=?',(trace_id,)).fetchone()[0]
            if count>=500:
                self.db.execute('UPDATE request_traces SET dropped_events=dropped_events+1 WHERE id=?',(trace_id,))
                return
            self.db.execute('INSERT INTO trace_events(trace_id,ts,event,details) VALUES(?,?,?,?)',(trace_id,time.time(),event,json.dumps(details)))

    def traces(self,*,start=-1e30,end=1e30,limit=100):
        with self._lock:
            rows=self.db.execute('SELECT * FROM request_traces WHERE ts>=? AND ts<? ORDER BY ts DESC,id DESC LIMIT ?',(start,end,max(1,min(limit,100)))).fetchall()
            result=[]
            for row in rows:
                item=dict(row)
                events=self.db.execute('SELECT details FROM trace_events WHERE trace_id=? ORDER BY id',(row['id'],)).fetchall()
                item['providers']=sorted({d['provider'] for e in events if (d:=json.loads(e['details'])).get('provider')})
                result.append(item)
            return result

    def trace_detail(self,trace_id):
        with self._lock:
            row=self.db.execute('SELECT * FROM request_traces WHERE id=?',(trace_id,)).fetchone()
            if row is None:
                return None
            result=dict(row)
            result['events']=[{**dict(e),'details':json.loads(e['details'])} for e in self.db.execute('SELECT id,ts,event,details FROM trace_events WHERE trace_id=? ORDER BY id',(trace_id,))]
            call_ids=[e['details']['call_id'] for e in result['events'] if e['event']=='reserved']
            result['calls']=[]
            for call_id in dict.fromkeys(call_ids):
                call=self.db.execute('SELECT * FROM calls WHERE id=?',(call_id,)).fetchone()
                if call:
                    allocations=[{**dict(a),'unit':json.loads(a['identity'])['unit']} for a in self.db.execute('SELECT a.pool_id,a.reserved,a.charged,p.identity FROM allocations a JOIN pools p ON p.id=a.pool_id WHERE call_id=?',(call_id,))]
                    for allocation in allocations:
                        allocation.pop('identity')
                    result['calls'].append({**dict(call),'allocations':allocations})
            return result

    def fingerprint(self, provider: str, secret: str) -> str:
        return hmac.new(bytes.fromhex(self.salt), f"{provider}\0{secret}".encode(), hashlib.sha256).hexdigest()

    def register(self, config: AccountConfig, secrets_by_key: dict[str, str]) -> None:
        accounts = {a.id: a for a in config.accounts}
        seen = set()
        with self.transaction():
            for pool in config.pools:
                # Budget and window identity cannot silently change underneath
                # active processes; a new pool requires an explicit new baseline.
                identity = pool.model_dump_json()
                row = self.db.execute("SELECT identity FROM pools WHERE id=?", (pool.id,)).fetchone()
                if row and row[0] != identity:
                    original=QuotaPool.model_validate_json(row[0])
                    if original==pool:
                        self.db.execute('UPDATE pools SET identity=? WHERE id=?',(identity,pool.id))
                        continue
                    has_calls=self.db.execute("SELECT 1 FROM allocations WHERE pool_id=? LIMIT 1",(pool.id,)).fetchone()
                    if original.window.kind!="unknown" or has_calls or original.account_id!=pool.account_id or original.unit!=pool.unit:
                        raise ValueError("existing quota pool configuration changed; reconciliation required")
                    self.db.execute("UPDATE pools SET identity=?,initial_used=?,created=? WHERE id=?",(identity,None if pool.initial_used is None else units(pool.initial_used),time.time(),pool.id))
                if pool.limit is not None:
                    units(pool.limit)
                initial = None if pool.initial_used is None else units(pool.initial_used)
                self.db.execute("INSERT OR IGNORE INTO pools(id,identity,initial_used,created) VALUES(?,?,?,?)",
                                (pool.id, identity, initial, time.time()))
            for key in config.keys:
                value = secrets_by_key.get(key.id, "")
                if not value:
                    continue
                fp = self.fingerprint(accounts[key.account_id].provider, value)
                if fp in seen:
                    raise ValueError("duplicate credential material in account configuration")
                seen.add(fp)
                row = self.db.execute("SELECT * FROM credentials WHERE id=?", (key.id,)).fetchone()
                other = self.db.execute("SELECT id FROM credentials WHERE fingerprint=? AND id<>?", (fp,key.id)).fetchone()
                if other:
                    raise ValueError("credential material already belongs to another key")
                if row and row["account_id"] != key.account_id:
                    raise ValueError("existing key cannot change its account")
                if row is None:
                    self.db.execute("INSERT INTO credentials(id,account_id,fingerprint) VALUES(?,?,?)", (key.id,key.account_id,fp))
                elif row["fingerprint"] != fp:
                    if accounts[key.account_id].provider=='jina' and any(pool.account_id==key.account_id and pool.free_allowance_confirmed for pool in config.pools):
                        raise ValueError('new Jina key cannot inherit a confirmed wallet budget')
                    self.db.execute("UPDATE credentials SET fingerprint=?,disabled=0,cooldown=0,reason='' WHERE id=?", (fp,key.id))
                if not key.enabled:
                    self.db.execute("UPDATE credentials SET disabled=1,reason='manual' WHERE id=?",(key.id,))

    def _remaining(self, pool: QuotaPool, now: float) -> int | None:
        row = self.db.execute("SELECT * FROM pools WHERE id=?", (pool.id,)).fetchone()
        if not row or row["blocked_until"] > now or not pool.free_allowance_confirmed or row["initial_used"] is None:
            return 0
        if pool.window.kind == "unknown":
            return 0
        if pool.window.kind == "unlimited":
            return None
        start, end = pool.window.bounds(datetime.fromtimestamp(now, timezone.utc))
        start_ts = start.timestamp() if start else -1e30
        end_ts = end.timestamp() if end else 1e30
        spent = self.db.execute(
            "SELECT COALESCE(SUM(CASE WHEN c.state='settled' THEN a.charged ELSE a.reserved END),0) "
            "FROM allocations a JOIN calls c ON c.id=a.call_id WHERE a.pool_id=? "
            "AND (c.state IN ('reserved','dispatched','uncertain') OR (c.state='settled' AND c.ts>=? AND c.ts<?))",
            (pool.id,start_ts,end_ts)).fetchone()[0]
        opening = row["initial_used"] if start_ts <= row["created"] < end_ts else 0
        return max(0,units(pool.limit)-spent-opening)

    def remaining(self, pool: QuotaPool, now: float | None = None) -> Decimal | None:
        with self._lock:
            moment = time.time() if now is None else now
            value = self._remaining(pool, moment)
            cap = self._account_cap(pool.account_id,moment)
            if cap is not None:
                value = cap if value is None else min(value,cap)
            return None if value is None else Decimal(value)/SCALE

    def _account_cap(self, account_id: str, now: float) -> int | None:
        snapshot=self.db.execute("SELECT * FROM balance_snapshots WHERE account_id=? ORDER BY id DESC LIMIT 1",(account_id,)).fetchone()
        if snapshot is None:
            return None
        if snapshot["expires"] <= now:
            return 0
        # Requests begun after the sync watermark cannot safely be assumed to
        # be included upstream. Pre-existing unsettled requests were reserved
        # separately at the start; retain that conservative bound as well.
        spent=self.db.execute(
            "SELECT COALESCE(SUM(CASE WHEN c.state='settled' THEN a.charged WHEN c.state='released' THEN 0 ELSE a.reserved END),0) "
            "FROM calls c JOIN allocations a ON a.call_id=c.id JOIN pools p ON p.id=a.pool_id "
            "WHERE json_extract(p.identity,'$.account_id')=? AND c.rowid>?",(account_id,snapshot["watermark"])).fetchone()[0]
        return max(0,snapshot["cap_units"]-snapshot["held"]-spent)

    def account_remaining(self, account_id: str, pools: list[QuotaPool]) -> Decimal:
        with self._lock:
            now=time.time()
            value=sum((self._remaining(p,now) or 0) for p in pools)
            cap=self._account_cap(account_id,now)
            return Decimal(min(value,cap) if cap is not None else value)/SCALE

    def forecast(self,pool: QuotaPool,*,now: float | None = None) -> dict | None:
        moment=time.time() if now is None else now
        if not pool.free_allowance_confirmed or pool.initial_used is None or pool.limit is None:
            return None
        with self._lock:
            created=self.db.execute('SELECT created FROM pools WHERE id=?',(pool.id,)).fetchone()
            if created is None or created[0]>moment-7*86400:
                return None
            row=self.db.execute(
                "SELECT COUNT(DISTINCT c.id),COUNT(DISTINCT date(c.ts,'unixepoch')),COALESCE(SUM(a.charged),0) "
                "FROM calls c JOIN allocations a ON a.call_id=c.id WHERE a.pool_id=? AND c.state='settled' AND c.success=1 AND a.charged>0 AND c.ts>=? AND c.ts<?",
                (pool.id,moment-7*86400,moment)).fetchone()
            if row[0]<5 or row[1]<2 or row[2]<=0:
                return None
            remaining=self.remaining(pool,moment)
            if remaining is None or remaining<=0:
                return None
            rate=Decimal(row[2])/SCALE/7
            days=float(remaining/rate)
            if days>365:
                return None
            exhaustion=moment+days*86400
            _,reset=pool.window.bounds(datetime.fromtimestamp(moment,timezone.utc))
            return {'sample_days':7,'sample_calls':row[0],'average_daily':str(rate),'unit':pool.unit,
                    'estimated_days':round(days,2),'estimated_exhaustion_at':exhaustion,
                    'before_reset':exhaustion<reset.timestamp() if reset else None,'source':'local_settled_usage'}

    def reserve(self, key: ApiKeyRef, pools: dict[str, QuotaPool], provider: str, endpoint: str, cost, *, fingerprint: str | None = None) -> str:
        required = units(cost)
        if required <= 0:
            raise ValueError("a positive conservative reservation is required")
        with self.transaction():
            now = time.time()
            state = self.db.execute("SELECT * FROM credentials WHERE id=?", (key.id,)).fetchone()
            if not key.enabled or not state or state["disabled"] or state["cooldown"] > now:
                raise BudgetUnavailable("key unavailable",reason="key_cooldown" if state and state["cooldown"]>now else "key_disabled")
            if fingerprint is not None and fingerprint!=state["fingerprint"]:
                raise BudgetUnavailable("credential changed before dispatch",reason="credential_changed")
            cap=self._account_cap(key.account_id,now)
            if cap is not None and required>cap:
                raise BudgetUnavailable("official free budget unavailable or stale",reason="official_budget_unavailable")
            plan = []
            left = required
            for pool_id in key.pool_ids:
                pool = pools[pool_id]
                available = self._remaining(pool, now)
                take = left if available is None else min(left, available)
                if take:
                    plan.append((pool_id,take))
                    left -= take
                if left == 0:
                    break
            if left:
                raise BudgetUnavailable("confirmed free budget unavailable",reason="pool_budget_unavailable")
            call_id = uuid4().hex
            self.db.execute("INSERT INTO calls(id,key_id,provider,endpoint,ts,state) VALUES(?,?,?,?,?,'reserved')",
                            (call_id,key.id,provider,endpoint,now))
            self.db.executemany("INSERT INTO allocations(call_id,pool_id,reserved) VALUES(?,?,?)",
                                [(call_id,pool_id,take) for pool_id,take in plan])
        return call_id

    def dispatch(self, call_id: str) -> None:
        with self.transaction():
            count = self.db.execute("UPDATE calls SET state='dispatched' WHERE id=? AND state='reserved'", (call_id,)).rowcount
            if count != 1:
                raise ValueError("reservation is not dispatchable")

    def settle(self, call_id: str, cost, *, success: bool = True, latency_ms: int = 0) -> None:
        actual = units(cost)
        with self.transaction():
            call = self.db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if not call:
                raise ValueError("unknown reservation")
            rows = self.db.execute("SELECT * FROM allocations WHERE call_id=? ORDER BY rowid", (call_id,)).fetchall()
            if call["state"] == "settled":
                if sum(r["charged"] for r in rows) != actual or bool(call["success"]) != success:
                    raise ValueError("conflicting repeated settlement")
                return
            if call["state"] not in ("dispatched", "uncertain"):
                raise ValueError("reservation has not been dispatched")
            reserved = sum(r["reserved"] for r in rows)
            left = actual
            for index,row in enumerate(rows):
                charge = left if index == len(rows)-1 else min(left,row["reserved"])
                self.db.execute("UPDATE allocations SET charged=? WHERE call_id=? AND pool_id=?", (charge,call_id,row["pool_id"]))
                left -= charge
            if actual > reserved:
                self.db.executemany("UPDATE pools SET blocked_until=1e30 WHERE id=?", [(r["pool_id"],) for r in rows])
            self.db.execute("UPDATE calls SET state='settled',success=?,latency_ms=?,error=? WHERE id=?",
                            (int(success),latency_ms,"cost_bound_exceeded" if actual>reserved else "",call_id))

    def uncertain(self, call_id: str, error: str, *, latency_ms: int = 0) -> None:
        with self.transaction():
            self.db.execute("UPDATE calls SET state='uncertain',success=0,error=?,latency_ms=? WHERE id=? AND state IN ('reserved','dispatched')",
                            (error,latency_ms,call_id))

    def release(self, call_id: str) -> None:
        with self.transaction():
            count = self.db.execute("UPDATE calls SET state='released' WHERE id=? AND state='reserved'", (call_id,)).rowcount
            if count != 1:
                raise ValueError("only an undispatched reservation can be released")

    def penalize(self, key: ApiKeyRef, kind: str, *, fingerprint: str | None = None) -> None:
        with self.transaction():
            state=self.db.execute("SELECT * FROM credentials WHERE id=?",(key.id,)).fetchone()
            if state is None or (fingerprint is not None and state["fingerprint"]!=fingerprint) or (state["disabled"] and state["reason"]=="manual"):
                return
            if kind == "auth":
                self.db.execute("UPDATE credentials SET disabled=1,reason='auth' WHERE id=?", (key.id,))
            else:
                cooldown = time.time() + (300 if kind == "rate_limit" else 30)
                self.db.execute("UPDATE credentials SET cooldown=?,reason=? WHERE id=?", (cooldown,kind,key.id))
            if kind == "quota":
                for pool_id in key.pool_ids:
                    row = self.db.execute("SELECT identity FROM pools WHERE id=?",(pool_id,)).fetchone()
                    pool = QuotaPool.model_validate_json(row[0])
                    _,end = pool.window.bounds(datetime.now(timezone.utc))
                    self.db.execute("UPDATE pools SET blocked_until=? WHERE id=?",(end.timestamp() if end else 1e30,pool_id))

    def calls(self, *, limit: int = 100, start: float = -1e30, end: float = 1e30) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute("SELECT c.*,COALESCE(SUM(a.charged),0) AS charged_units,COALESCE(SUM(a.reserved),0) AS reserved_units FROM calls c LEFT JOIN allocations a ON a.call_id=c.id WHERE c.ts>=? AND c.ts<? GROUP BY c.id ORDER BY c.ts DESC LIMIT ?", (start,end,max(1,min(limit,1000))))]

    def monthly_stats(self, start: float, end: float) -> dict[str, dict]:
        with self._lock:
            rows=self.db.execute(
                "SELECT c.key_id,COUNT(DISTINCT c.id) AS calls,COUNT(DISTINCT CASE WHEN c.success=1 THEN c.id END) AS successes,"
                "COALESCE(SUM(CASE WHEN c.state='settled' THEN a.charged WHEN c.state='released' THEN 0 ELSE a.reserved END),0) AS consumed_units "
                "FROM calls c LEFT JOIN allocations a ON a.call_id=c.id WHERE c.ts>=? AND c.ts<? GROUP BY c.key_id",(start,end)).fetchall()
            return {r["key_id"]:dict(r) for r in rows}

    def key_states(self) -> dict[str, dict]:
        with self._lock:
            return {r["id"]:dict(r) for r in self.db.execute("SELECT id,disabled,cooldown,reason FROM credentials")}

    def begin_balance_sync(self, account_id: str, *, interval: int = 60) -> tuple[int,int]:
        with self.transaction():
            now=time.time()
            row=self.db.execute("SELECT next_allowed FROM balance_sync WHERE account_id=?",(account_id,)).fetchone()
            if row and row[0]>now:
                raise BudgetUnavailable("balance synchronization is rate limited")
            if self.db.execute("SELECT COUNT(*) FROM balance_sync WHERE status='syncing' AND next_allowed>?",(now,)).fetchone()[0]>=3:
                raise BudgetUnavailable("balance synchronization is globally busy")
            self.db.execute("INSERT INTO balance_sync(account_id,last_attempt,next_allowed,status) VALUES(?,?,?,'syncing') ON CONFLICT(account_id) DO UPDATE SET last_attempt=excluded.last_attempt,next_allowed=excluded.next_allowed,status='syncing',error=''",(account_id,now,now+interval))
            watermark=self.db.execute("SELECT COALESCE(MAX(rowid),0) FROM calls").fetchone()[0]
            held=self.db.execute(
                "SELECT COALESCE(SUM(a.reserved),0) FROM calls c JOIN allocations a ON a.call_id=c.id JOIN pools p ON p.id=a.pool_id "
                "WHERE json_extract(p.identity,'$.account_id')=? AND c.state IN ('reserved','dispatched','uncertain')",(account_id,)).fetchone()[0]
            return watermark,held

    def save_balance(self, account_id: str, key_id: str, snapshot: dict, watermark: int, held: int, *, ttl: int = 600):
        cap=units(snapshot["remaining"]) if uses_total_ceiling(snapshot) else units(snapshot["free_remaining"]) if snapshot["free_remaining"] is not None else 0
        now=time.time()
        expires=min(now+ttl,snapshot.get("period_end") or now+ttl)
        with self.transaction():
            if uses_total_ceiling(snapshot):
                pool_units={json.loads(row['identity'])['unit'] for row in self.db.execute("SELECT identity FROM pools WHERE json_extract(identity,'$.account_id')=?",(account_id,))}
                if pool_units!={snapshot['unit']}:
                    raise ValueError('official total ceiling requires USD-only or credits-only matching account pools')
            self.db.execute("INSERT INTO balance_snapshots(account_id,key_id,observed,watermark,held,cap_units,expires,snapshot) VALUES(?,?,?,?,?,?,?,?)",
                            (account_id,key_id,now,watermark,held,cap,expires,json.dumps(snapshot)))
            self.db.execute("UPDATE balance_sync SET status='ok',error='',failures=0 WHERE account_id=?",(account_id,))

    def fail_balance_sync(self, account_id: str, error: str):
        with self.transaction():
            if error=='cancelled':
                self.db.execute("UPDATE balance_sync SET status='cancelled',error='cancelled' WHERE account_id=?",(account_id,))
                return
            row=self.db.execute("SELECT failures FROM balance_sync WHERE account_id=?",(account_id,)).fetchone()
            if row is None:
                return
            failures=row[0]+1
            delay=min(900,60*2**min(failures-1,4))
            self.db.execute("UPDATE balance_sync SET status='failed',error=?,failures=?,next_allowed=? WHERE account_id=?",(error,failures,time.time()+delay,account_id))

    def balances(self) -> dict[str,dict]:
        with self._lock:
            states={r["account_id"]:dict(r) for r in self.db.execute("SELECT * FROM balance_sync")}
            for row in self.db.execute("SELECT b.* FROM balance_snapshots b JOIN (SELECT account_id,MAX(id) AS id FROM balance_snapshots GROUP BY account_id) latest ON b.id=latest.id"):
                entry=states.setdefault(row["account_id"],{})
                entry.update(json.loads(row["snapshot"]))
                entry.update(observed_at=row["observed"],expires_at=row["expires"],stale=row["expires"]<=time.time())
            return states

    def balance_history(self, start: float, end: float) -> list[dict]:
        with self._lock:
            return [{"id":r["id"],"account_id":r["account_id"],"observed_at":r["observed"],**json.loads(r["snapshot"])} for r in self.db.execute("SELECT * FROM balance_snapshots WHERE observed>=? AND observed<? ORDER BY id DESC LIMIT 100",(start,end))]

    def update_alerts(self, alerts: list[dict], *, now: float | None = None):
        moment=time.time() if now is None else now
        current={item['id']:item for item in alerts}
        with self.transaction():
            existing={row['id']:dict(row) for row in self.db.execute('SELECT * FROM alerts')}
            for identity,item in current.items():
                old=existing.get(identity)
                details=json.dumps(item['details'],ensure_ascii=False,sort_keys=True)
                transition=old is None or not old['active'] or old['severity']!=item['severity']
                if old is None:
                    self.db.execute('INSERT INTO alerts VALUES(?,?,?,?,?,1,1,?,?,NULL,NULL,?)',
                                    (identity,item['scope_kind'],item['scope_id'],item['kind'],item['severity'],moment,moment,details))
                else:
                    self.db.execute('UPDATE alerts SET active=1,severity=?,version=version+?,last_seen=?,resolved_at=NULL,dismissed_at=CASE WHEN ? THEN NULL ELSE dismissed_at END,details=? WHERE id=?',
                                    (item['severity'],int(transition),moment,int(transition),details,identity))
                if transition:
                    self.db.execute('INSERT INTO alert_events(alert_id,ts,event,severity,details) VALUES(?,?,?,?,?)',
                                    (identity,moment,'activated' if old is None or not old['active'] else 'escalated',item['severity'],details))
            for identity,old in existing.items():
                if old['active'] and identity not in current:
                    self.db.execute('UPDATE alerts SET active=0,resolved_at=?,last_seen=? WHERE id=?',(moment,moment,identity))
                    self.db.execute('INSERT INTO alert_events(alert_id,ts,event,severity,details) VALUES(?,?,?,?,?)',(identity,moment,'resolved',old['severity'],old['details']))

    def active_alerts(self) -> list[dict]:
        with self._lock:
            return [{**dict(row),'details':json.loads(row['details'])} for row in self.db.execute("SELECT * FROM alerts WHERE active=1 ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,first_seen DESC")]

    def alert_history(self, *, limit=50) -> list[dict]:
        with self._lock:
            return [{**dict(row),'details':json.loads(row['details'])} for row in self.db.execute('SELECT e.*,a.scope_kind,a.scope_id FROM alert_events e LEFT JOIN alerts a ON a.id=e.alert_id ORDER BY e.id DESC LIMIT ?',(max(1,min(limit,100)),))]

    def dismiss_alert(self, identity: str, version: int):
        with self.transaction():
            row=self.db.execute('SELECT * FROM alerts WHERE id=? AND version=? AND active=1',(identity,version)).fetchone()
            if row is None:
                raise ValueError('alert changed; reload before dismissing')
            if row['dismissed_at'] is None:
                now=time.time()
                self.db.execute('UPDATE alerts SET dismissed_at=? WHERE id=?',(now,identity))
                self.db.execute("INSERT INTO alert_events(alert_id,ts,event,severity,details) VALUES(?,?,'dismissed',?,?)",(identity,now,row['severity'],row['details']))

    def close(self):
        self.db.close()
