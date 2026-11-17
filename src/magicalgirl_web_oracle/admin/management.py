"""Transactional configuration edits and encrypted credentials; no secret export."""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr

from ..quota.accounts import AccountConfig, ApiKeyRef, Amount, BillingWindow, Contract, Identifier, Provider, ProviderAccount, QuotaPool


class ConfigConflict(ValueError):
    pass


class KeyCreate(Contract):
    revision: int = Field(ge=0)
    label: str = Field(min_length=1,max_length=100)
    secret: SecretStr = Field(min_length=1,max_length=4096,repr=False,exclude=True)
    account_id: Identifier | None = None
    provider: Provider | None = None
    account_label: str = Field(default="",max_length=100)
    owner_label: str = Field(default="",max_length=100)


class KeyPatch(Contract):
    revision: int = Field(ge=0)
    label: str | None = Field(default=None,min_length=1,max_length=100)
    enabled: bool | None = None
    secret: SecretStr | None = Field(default=None,min_length=1,max_length=4096,repr=False,exclude=True)


class PoolConfirm(Contract):
    revision: int = Field(ge=0)
    limit: Amount
    initial_used: Amount
    window: BillingWindow
    free_allowance_confirmed: bool


def document(config):
    value=config.model_dump(mode="json")
    for row,key in zip(value["keys"],config.keys):
        row["secret_env"]=key.secret_env
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False)


class KeyManager:
    def __init__(self,executor,master_key: str):
        self.executor=executor
        self.base=executor.config
        self.base_secrets=executor._secrets.copy()
        self.base_hash=hashlib.sha256(document(self.base).encode()).hexdigest()
        self.enabled=bool(master_key)
        try:
            self.crypto=Fernet(master_key.encode()) if master_key else None
        except Exception:
            raise ValueError("invalid ORACLE_VAULT_KEY; expected a Fernet master key") from None
        self.reload(force=True)

    def reload(self,*,force=False):
        executor=self.executor
        with executor._config_lock,executor.ledger.transaction():
            db=executor.ledger.db
            saved=db.execute("SELECT * FROM managed_config WHERE id=1").fetchone()
            if saved is None:
                executor.revision=0
                return
            if not self.enabled:
                raise ValueError("ORACLE_VAULT_KEY required to unlock managed credentials")
            if saved["base_hash"]!=self.base_hash:
                raise ValueError("base account configuration changed; reconcile managed configuration first")
            if not force and executor.revision==saved["revision"]:
                return
            try:
                config=AccountConfig.model_validate_json(saved["document"])
                secrets_by_key={key.id:self.base_secrets.get(key.id,"") for key in config.keys}
                for row in db.execute("SELECT * FROM managed_secrets"):
                    if row["key_id"] in secrets_by_key:
                        secrets_by_key[row["key_id"]]=self.crypto.decrypt(row["ciphertext"].encode()).decode()
            except Exception:
                raise ValueError("managed credentials could not be unlocked; check the master key and backup") from None
            executor.ledger.register(config,secrets_by_key)
            executor.apply_config(config,secrets_by_key,saved["revision"])

    def _save(self,config,secrets_by_key,revision,*,changed_secret=None,manual_state=None):
        executor=self.executor
        if not self.enabled:
            raise ValueError("credential management is disabled")
        # Validate copies created by model_copy as well as external inputs.
        config=AccountConfig.model_validate_json(document(config))
        with executor.ledger.transaction():
            db=executor.ledger.db
            saved=db.execute("SELECT revision FROM managed_config WHERE id=1").fetchone()
            current=saved[0] if saved else 0
            if revision!=current:
                raise ConfigConflict("configuration changed; reload before editing")
            executor.ledger.register(config,secrets_by_key)
            if changed_secret:
                key_id,secret=changed_secret
                cipher=self.crypto.encrypt(secret.encode()).decode()
                db.execute("INSERT INTO managed_secrets VALUES(?,?) ON CONFLICT(key_id) DO UPDATE SET ciphertext=excluded.ciphertext",(key_id,cipher))
            if manual_state:
                key_id,enabled=manual_state
                if enabled:
                    db.execute("UPDATE credentials SET disabled=0,reason='' WHERE id=? AND reason='manual'",(key_id,))
                else:
                    db.execute("UPDATE credentials SET disabled=1,reason='manual' WHERE id=?",(key_id,))
            db.execute("INSERT INTO managed_config VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,document=excluded.document",(revision+1,self.base_hash,document(config)))
        executor.apply_config(config,secrets_by_key,revision+1)

    def create(self,request: KeyCreate):
        executor=self.executor
        with executor._config_lock:
            self.reload()
            config=executor.config
            accounts=list(config.accounts);pools=list(config.pools)
            if request.account_id:
                account=executor.accounts.get(request.account_id)
                if account is None:
                    raise ValueError("unknown account")
                if account.provider=="jina":
                    raise ValueError("each Jina key requires an independent wallet account")
                if request.provider is not None and request.provider!=account.provider:
                    raise ValueError("account provider mismatch")
                pool_ids=tuple(pool.id for pool in pools if pool.account_id==account.id)
                if not pool_ids:
                    raise ValueError("account has no quota pool")
            else:
                if not request.provider or not request.account_label.strip():
                    raise ValueError("new account requires provider and name")
                account=ProviderAccount(id="account-"+uuid4().hex,provider=request.provider,label=request.account_label.strip(),owner_label=request.owner_label)
                pool=QuotaPool(id="pool-"+uuid4().hex,account_id=account.id,unit={"exa":"usd","jina":"tokens","you":"usd"}.get(account.provider,"credits"))
                accounts.append(account);pools.append(pool);pool_ids=(pool.id,)
            key=ApiKeyRef(id="key-"+uuid4().hex,account_id=account.id,label=request.label,pool_ids=pool_ids,secret_env="ORACLE_VAULT_"+uuid4().hex.upper())
            secret=request.secret.get_secret_value().strip()
            if not secret:
                raise ValueError("credential cannot be blank")
            values={**executor._secrets,key.id:secret}
            new=config.model_copy(update={"accounts":tuple(accounts),"pools":tuple(pools),"keys":(*config.keys,key)})
            self._save(new,values,request.revision,changed_secret=(key.id,secret))
            return key.id

    def patch(self,key_id: str,request: KeyPatch):
        executor=self.executor
        with executor._config_lock:
            self.reload()
            key=next((key for key in executor.config.keys if key.id==key_id),None)
            if key is None:
                raise ValueError("unknown key")
            changes={field:getattr(request,field) for field in ("label","enabled") if getattr(request,field) is not None}
            if not changes and request.secret is None:
                raise ValueError("no changes supplied")
            state=executor.ledger.key_states().get(key_id,{})
            if request.enabled and state.get("reason")=="auth" and request.secret is None:
                raise ValueError("replace invalid credentials before enabling this key")
            changed=None;values=executor._secrets.copy()
            if request.secret is not None:
                secret=request.secret.get_secret_value().strip()
                if executor.accounts[key.account_id].provider=='jina' and secret!=values[key_id] and any(pool.account_id==key.account_id and pool.free_allowance_confirmed for pool in executor.config.pools):
                    raise ValueError('new Jina key requires an independent wallet; budget is not transferable')
                if not secret:
                    raise ValueError("credential cannot be blank")
                values[key_id]=secret;changed=(key_id,secret)
            keys=tuple(key.model_copy(update=changes) if key.id==key_id else key for key in executor.config.keys)
            self._save(executor.config.model_copy(update={"keys":keys}),values,request.revision,changed_secret=changed,
                       manual_state=(key_id,request.enabled) if request.enabled is not None else None)

    def confirm_pool(self,pool_id: str,request: PoolConfirm):
        executor=self.executor
        with executor._config_lock:
            self.reload()
            pool=executor.pools.get(pool_id)
            if pool is None or not request.free_allowance_confirmed or request.window.kind not in ("daily","monthly","rolling","one_time"):
                raise ValueError("a verified finite free allowance is required")
            new=pool.model_copy(update=request.model_dump(exclude={"revision"}))
            # Preserve the typed window rather than its serialized dictionary.
            new=new.model_copy(update={"window":request.window})
            pools=tuple(new if item.id==pool_id else item for item in executor.config.pools)
            self._save(executor.config.model_copy(update={"pools":pools}),executor._secrets.copy(),request.revision)
