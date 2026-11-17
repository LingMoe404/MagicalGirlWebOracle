# 多账户与多 Key 配置

[English](multi-key-setup.md) · **简体中文** · [日本語](multi-key-setup.ja.md)

默认情况下，Oracle 使用 `.env` 中每个供应商的单个 Key。多账户模式需要显式开启，支持：

- 登记多个供应商账户（例如两个 Google 账户）；
- 建模共享或独立的额度池；
- 一个池绑定多个 Key；
- 在管理台添加、替换、禁用和轮换 Key，无需编辑 `.env`；
- 保留可审计的逐 Key、逐池用量账本。

该模式面向免费额度：额度池在确认当前额度前处于**不可用**状态，未知预算直接跳过，而不是乐观消耗。

## 数据模型

```text
账户标签（例如 Google-A）
  └─ 供应商账户 / 组织（例如 Tavily-A）
      ├─ 额度池（每月免费额度、一次性赠送等）
      └─ API Key（一个或多个；每次调用归属到具体 Key）
```

- 同一供应商账户下的 Key 默认共享一个池。
- 只有供应商确实提供独立额度时才拆池。
- 不同单位的余额不能相加：USD、credits、tokens、requests 分别跟踪。
- Jina Reader/Reranker 的每个 Key 视为独立钱包；钱包不能跨 Key 共享。

## 启用多账户模式

1. 从示例创建 `data/accounts.yaml`：

   ```bash
   cp config/accounts.example.yaml data/accounts.yaml
   ```

2. 指向该文件：

   ```ini
   ORACLE_ACCOUNTS_FILE=/app/data/accounts.yaml
   ```

3. 重启服务。

YAML 只包含引用，绝不包含真实 Key。Key 的 `secret_env` 指向保存密钥的环境变量：

```yaml
version: 1
accounts:
  - id: tavily-a
    provider: tavily
    label: Tavily primary
    owner_label: Google-A
  - id: tavily-b
    provider: tavily
    label: Tavily secondary
    owner_label: Google-B
pools:
  - id: tavily-a-monthly
    account_id: tavily-a
    unit: credits
    limit: null
    initial_used: null
    free_allowance_confirmed: false
    window: { kind: unknown }
  - id: tavily-b-monthly
    account_id: tavily-b
    unit: credits
    limit: null
    initial_used: null
    free_allowance_confirmed: false
    window: { kind: unknown }
keys:
  - id: tavily-a-primary
    account_id: tavily-a
    label: Primary key
    secret_env: TAVILY_API_KEY
    pool_ids: [tavily-a-monthly]
    priority: 10
  - id: tavily-b-primary
    account_id: tavily-b
    label: Secondary key
    secret_env: TAVILY_API_KEY_SECOND
    pool_ids: [tavily-b-monthly]
    priority: 20
```

更多示例：

- `config/accounts.example.yaml` —— 两个 Tavily 账户，独立额度池。
- `config/accounts.you.example.yaml` —— 两个 You.com USD 钱包。
- `config/accounts.jina.example.yaml` —— 两个独立 Jina token 钱包。

### 确认额度池

`limit: null`、`initial_used: null` 或 `free_allowance_confirmed: false` 的池不可消费。启用前必须确认**当前**免费额度、已消耗量、账单日和时区。推荐在管理台确认，或编辑 YAML 后重启。

确认后，账本会在每次调用前预留预算、调用后结算。未知或未确认的预算会被跳过。

## 密钥来源：环境变量 vs 加密账本

支持两种方式：

1. **环境变量** —— YAML 中的 `secret_env` 指向环境变量。简单，但密钥仍在 `.env`。
2. **管理台 + 加密账本** —— 设置 `ORACLE_VAULT_KEY`，在 Web 管理台添加 Key。密钥以 Fernet 密文存入 SQLite 账本，主密钥只存在于环境变量中，必须单独备份。

管理台提交后不会回显完整 Key。响应、HTML、前端产物和日志只包含脱敏标识。

> 丢失 `ORACLE_VAULT_KEY` 会导致加密凭据无法恢复。已有加密账本时不要重新生成它。

## 管理台

设置：

```ini
ORACLE_ADMIN_TOKEN=<长随机令牌>
ORACLE_VAULT_KEY=<fernet-key>
ORACLE_ACCOUNTS_FILE=/app/data/accounts.yaml
```

管理令牌必须与 `MCP_AUTH_TOKEN` 不同。启动可选 HTTPS 监听：

```bash
docker compose --profile admin up -d
```

打开 `https://127.0.0.1:28443/admin/`。证书配置见 [deployment.zh-CN.md](deployment.zh-CN.md#可选管理台)。

管理台提供：

- **额度总览** —— 逐账户/Key 的剩余预算、单位、账期，以及数据是官方值还是本地估算。
- **Key 管理** —— 添加 Key 到已有共享池或新账户，重命名、禁用、替换凭据；期初用量需要显式确认。
- **调用记录与追踪** —— 供应商、Key、账户、状态、延迟、预留/结算成本、候选顺序和切换原因。
- **余额快照** —— 逐账户官方余额查询；失败或过期快照保留，不显示为 0。
- **提醒** —— 低余额、凭据失效、连续失败和保守的耗尽预测。
- **缓存与调度** —— 当前策略、TTL 和缓存计数。

## 官方余额同步

启用多账户后，后台调度器会查询 Tavily、Firecrawl、You.com 和 Jina 的官方余额。用 `ORACLE_BALANCE_SYNC_INTERVAL` 设置间隔（秒；`0` 关闭，否则 60–480）。

- Exa 没有公开余额 API，只能根据响应里的 `costDollars` 本地估算。
- Firecrawl / You.com 的官方总额不一定能拆分免费和付费来源，只作为上限，不自动解锁免费预算。
- 同步失败保留上次快照并标记过期，不会把余额重置为满额。

## 缓存、调度、导出与备份

```bash
# 搜索缓存 300 秒，抓取缓存 3600 秒
ORACLE_CACHE_TTL_SECONDS=300
ORACLE_FETCH_CACHE_TTL_SECONDS=3600

# priority | balanced | expiry | preferred_account
ORACLE_SCHEDULING_STRATEGY=balanced

# 导出脱敏历史（CSV 或 JSON）
docker compose exec oracle magicalgirl-web export --month 2026-11 --format csv

# 在线备份 + 校验
docker compose exec oracle magicalgirl-web backup \
  --db /app/data/usage.accounts.sqlite3 \
  -o /app/data/backup/usage.accounts.sqlite3
docker compose exec oracle magicalgirl-web verify-backup \
  /app/data/backup/usage.accounts.sqlite3
```

导出不包含 query 原文、明文 URL、凭据、指纹或主密钥。缓存命中不会调用供应商，也不消耗额度。

## 失败行为

- **供应商级失败**（超时、认证、限流、服务端错误）记入供应商健康熔断，并可能让 Key 进入短暂冷却。
- **目标级失败**（死链、目标返回 404/422、反爬墙）标记为 `kind="target"`，不会降低供应商健康，也不会冷却 Key；路由会升级到下一个抓取后端。
- **异步抓取创建**结果不明时不会盲目重试，避免重复任务和重复计费。
- 已发出但结果未知的请求在账本中保持 `uncertain`，预算不会被静默释放。

## 备份与恢复

备份：

- 账本（`data/usage.accounts.sqlite3`）；
- `data/accounts.yaml`；
- `.env`（供应商 Key 和管理令牌）；
- `ORACLE_VAULT_KEY`（与账本分开）；
- `data/private/tls/`（如果使用内置 HTTPS 管理监听）。

恢复时停止服务，然后运行 `verify-backup` 再信任恢复的账本。见 [deployment.zh-CN.md](deployment.zh-CN.md#备份与恢复)。

## 排障

| 症状 | 原因 | 修复 |
|---|---|---|
| 管理台显示 Key 管理已关闭 | `ORACLE_VAULT_KEY` 为空 | 设置后重启；保持稳定 |
| 管理台无法访问 | 管理令牌为空或 TLS 文件缺失 | 设置 `ORACLE_ADMIN_TOKEN`，提供证书/私钥，启动 `admin` profile |
| 额度池从不消费 | 免费额度未确认 | 确认上限、期初用量和账期 |
| Key 被跳过 | 禁用、缺少密钥、单位不匹配或冷却中 | 查看调用追踪和 UI 中的 Key 状态 |
| Jina Key 被拒绝 | Jina 钱包不能跨 Key 共享 | 每个 Jina Key 使用独立账户/钱包 |
| 本地余额低于官方 | 本地账本包含在途或未决调用 | 这是有意保守；在余额历史页面对账 |
