# 配置

[English](configuration.md) · **简体中文** · [日本語](configuration.ja.md)

MagicalGirlWebOracle 从两处读取配置：

1. **环境变量** —— 通常是当前工作目录（源码运行时是项目根目录）下的 `.env` 文件。
2. **YAML 文件** —— `config/providers.yaml` 和 `config/router_policy.yaml`。通过 wheel 安装时，如果没有本地 `config/` 目录，则使用 `magicalgirl_web_oracle/config/defaults/` 中的内置默认值。

真实密钥只应放在 `.env`、环境变量或加密账户账本中，绝不提交到仓库。

## 环境变量

### 供应商 Key（全部可选）

某个供应商的 Key 存在时才会启用；You.com 和 Jina Reader 还支持无 Key 模式。缺少 Key 只会把该供应商从路由中移除。

| 变量 | 用途 |
|---|---|
| `EXA_API_KEY` | Exa 搜索 / contents |
| `TAVILY_API_KEY` | Tavily 搜索 |
| `FIRECRAWL_API_KEY` | Firecrawl 抓取 / 爬取（最后手段） |
| `JINA_API_KEY` | Jina Reader 和（可选）重排 |
| `YDC_API_KEY` | You.com 带 Key 配置；无 Key 的 MCP 模式也可用 |

### 自建服务

| 变量 | 默认值 | 用途 |
|---|---|---|
| `SEARXNG_URL` | 空 | SearXNG 地址（必须开启 JSON 输出） |
| `CRAWL4AI_URL` | 空 | Crawl4AI API 地址 |
| `CRAWL4AI_TOKEN` | 空 | Crawl4AI 的 Bearer Token（如部署需要） |
| `PROXY_URL` | 空 | 云端供应商的出站代理；自建地址自动绕过 |

`PROXY_URL` 会回退到 `HTTPS_PROXY` / `HTTP_PROXY`。发往 `SEARXNG_URL` 和 `CRAWL4AI_URL` 的请求始终直连，不经过代理。

### 行为

| 变量 | 默认值 | 用途 |
|---|---|---|
| `ALLOW_PAID_OVERAGE` | `false` | 硬规则，保持 false；不支持付费超支 |
| `SEARCH_MODE_DEFAULT` | `auto` | `fast` / `auto` / `high` / `max` |
| `USAGE_DB_PATH` | `data/usage.sqlite3` | SQLite 用量账本 |
| `STORE_QUERY_TEXT` | `false` | 是否保存 query 原文（默认只存加盐哈希） |
| `QUERY_HASH_SALT` | 内置回退值 | 长期保存哈希时建议设置自己的随机盐 |
| `JINA_KEYLESS_FALLBACK` | `true` | 没有 Jina Key 时允许低速率无 Key 调用 `r.jina.ai` |

### MCP 传输与访问

| 变量 | 默认值 | 用途 |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio`、`streamable-http` 或 `sse` |
| `MCP_HOST` | `127.0.0.1` | 直接运行 HTTP 时的绑定地址；设为 `0.0.0.0` 才对外暴露 |
| `MCP_PORT` | `28288` | HTTP 端口 |
| `MCP_PATH` | `/mcp` | HTTP 路径 |
| `MCP_BIND_IP` | `127.0.0.1` | Docker Compose 发布端口使用的主机接口 |
| `MCP_AUTH_TOKEN` | 空 | 设置后客户端必须携带 Bearer Token |

共享部署时请设置足够长的随机 `MCP_AUTH_TOKEN`，并且只在需要的接口（局域网、VPN 或回环）上发布端口。见 [deployment.zh-CN.md](deployment.zh-CN.md) 和 [../SECURITY.zh-CN.md](../SECURITY.zh-CN.md)。

### 多账户 / 管理台（可选）

| 变量 | 默认值 | 用途 |
|---|---|---|
| `ORACLE_ACCOUNTS_FILE` | 空 | 多账户 YAML 路径（如 `data/accounts.yaml`） |
| `ORACLE_ADMIN_TOKEN` | 空 | `/admin/` 的独立 Bearer Token；为空则关闭管理台 |
| `ORACLE_VAULT_KEY` | 空 | Web 管理凭据使用的 Fernet 主密钥 |
| `ORACLE_ADMIN_BIND_IP` | `127.0.0.1` | Docker Compose 为管理监听发布端口使用的主机接口 |
| `ORACLE_ADMIN_HTTPS_PORT` | `28443` | 内置 HTTPS 管理监听端口 |
| `ORACLE_ADMIN_TLS_CERT` | `/app/data/private/tls/cert.pem` | TLS 证书 |
| `ORACLE_ADMIN_TLS_KEY` | `/app/data/private/tls/key.pem` | TLS 私钥 |
| `ORACLE_BALANCE_SYNC_INTERVAL` | `300` | 官方余额同步间隔（秒）；`0` 关闭，否则 60–480 |
| `ORACLE_LOW_QUOTA_RATIO` | `0.1` | 低余额提醒阈值，`0`–`1`；`0` 关闭 |

`ORACLE_ADMIN_TOKEN` 必须与 `MCP_AUTH_TOKEN` 不同。管理监听不暴露 `/mcp`。

### 缓存与调度

| 变量 | 默认值 | 用途 |
|---|---|---|
| `ORACLE_CACHE_TTL_SECONDS` | `0` | 搜索缓存 TTL；`0` 关闭 |
| `ORACLE_FETCH_CACHE_TTL_SECONDS` | 搜索缓存启用时 `3600`，否则 `0` | 抓取缓存 TTL |
| `ORACLE_CACHE_MAX_ENTRIES` | `500` | 最大缓存条目数（10–10000） |
| `ORACLE_CACHE_PATH` | 与用量账本同目录 | 独立 SQLite 缓存文件 |
| `ORACLE_SCHEDULING_STRATEGY` | `priority` | `priority`、`balanced`、`expiry` 或 `preferred_account` |
| `ORACLE_PREFERRED_ACCOUNT` | 空 | 策略为 `preferred_account` 时必填 |

缓存命中不会调用供应商，也不消耗额度。时效性查询（如 `latest`、`today`、`live`、`最新`、`今天`、`实时`）会自动跳过缓存。

## YAML 配置

### `config/providers.yaml`

每个供应商的免费额度元数据：额度类型、上限、单位、是否续期、成本等级、能力和备注。路由使用这些数据评分并判断请求是否可证明在免费额度内。不要把 API Key 写进这个文件。

### `config/router_policy.yaml`

评分权重、模式定义（`fast`、`auto`、`high`、`max`）、中文查询加权、抓取升级顺序和付费超支保护。默认策略偏保守：优先便宜和自建供应商，未知额度直接跳过。

## 多账户配置

多账户模式需要显式开启，并通过 `ORACLE_ACCOUNTS_FILE` 指向独立的 YAML 文件。该文件描述账户、额度池和 Key；管理页可以在不编辑 `.env` 的情况下添加和轮换 Key。schema、Web 管理流程和备份/恢复说明见 [multi-key-setup.zh-CN.md](multi-key-setup.zh-CN.md)。

## 检查配置

```bash
magicalgirl-web status      # 供应商健康度、额度和脱敏配置
magicalgirl-web doctor      # 连通性与配置诊断
magicalgirl-web doctor --json
```

两条命令都不会输出供应商 Key 或 Vault Key。
