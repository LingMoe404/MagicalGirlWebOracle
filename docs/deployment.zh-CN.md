# 部署

[English](deployment.md) · **简体中文** · [日本語](deployment.ja.md)

本文覆盖 Docker 部署；也可以直接用 Python 运行（环境变量见 [configuration.zh-CN.md](configuration.zh-CN.md)）。

## 前置条件

- Docker Engine + Compose 插件（或 Docker Desktop）。
- 可选但推荐：开启 JSON 输出的 SearXNG，和/或 Crawl4AI。没有它们服务仍可运行，但会失去自建搜索和浏览器级抓取兜底。
- 至少一个供应商 Key，或无 Key 的 You.com / Jina Reader 访问能力。

## 快速开始（Docker Compose）

```bash
git clone https://github.com/LingMoe404/MagicalGirlWebOracle.git
cd MagicalGirlWebOracle
cp .env.example .env
```

编辑 `.env`：

- 如果其他机器会连接，设置足够长的随机 `MCP_AUTH_TOKEN`。
- 如果运行 SearXNG / Crawl4AI，设置 `SEARXNG_URL` / `CRAWL4AI_URL`。
- 填入你拥有的供应商 Key。

启动：

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:28288/healthz
```

端口默认发布到 `127.0.0.1`。要让可信局域网或 VPN 中的其他机器访问，把 `MCP_BIND_IP` 改成对应接口地址（如 `0.0.0.0` 或 VPN 地址），并保持 `MCP_AUTH_TOKEN` 开启。

### 使用已发布镜像

```bash
# 先创建 .env
ORACLE_IMAGE=lingmoe404/magicalgirl-web-oracle:0.1.0 \
  docker compose up -d --no-build
```

镜像也有 `:latest` 标签。生产环境建议固定版本。

### 一键脚本

`deploy.sh` 会构建镜像、从容器内自动探测 SearXNG/Crawl4AI、首次运行时生成令牌、启动服务并执行 `magicalgirl-web doctor --json`：

```bash
bash deploy.sh
```

脚本是幂等的，并保留已有 `.env` 值。某些网络文件系统不保留执行位，所以用 `bash` 调用。

## 独立 Docker

stdio（单机，Agent 启动容器）：

```bash
docker run --rm -i \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  lingmoe404/magicalgirl-web-oracle:0.1.0
```

HTTP（共享服务）：

```bash
docker run -d --name magicalgirl-web-oracle \
  --env-file .env \
  -e MCP_TRANSPORT=streamable-http \
  -e MCP_HOST=0.0.0.0 \
  -e MCP_AUTH_TOKEN="$MCP_AUTH_TOKEN" \
  -p 127.0.0.1:28288:28288 \
  -v "$PWD/data:/app/data" \
  lingmoe404/magicalgirl-web-oracle:0.1.0
```

`data/` 卷保存用量账本、可选缓存，以及管理台使用的加密账户状态和 TLS 文件。请备份。

## MCP 客户端配置

服务在 `/mcp` 暴露 streamable-http。通用客户端配置：

```json
{
  "mcpServers": {
    "magicalgirl-web-oracle": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:28288/mcp",
      "headers": { "Authorization": "Bearer <MCP_AUTH_TOKEN>" }
    }
  }
}
```

`MCP_AUTH_TOKEN` 为空时省略 `headers`。stdio 客户端使用 `magicalgirl-web` 或 `python -m magicalgirl_web_oracle.mcp_server.server` 作为命令。可直接复制 [`examples/mcp/`](../examples/mcp/) 下的示例。

## 可选管理台

管理台是独立的可选 HTTPS 监听，与 MCP 进程共用用量账本，不暴露 `/mcp`。

1. 在 `.env` 中设置：

   ```ini
   ORACLE_ACCOUNTS_FILE=/app/data/accounts.yaml
   ORACLE_ADMIN_TOKEN=<长随机令牌>
   ORACLE_VAULT_KEY=<fernet-key>
   ORACLE_ADMIN_BIND_IP=127.0.0.1
   ORACLE_ADMIN_HTTPS_PORT=28443
   ```

   生成 Fernet 主密钥：

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. 生成自签名证书（或使用自己的证书）：

   ```bash
   mkdir -p data/private/tls
   openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
     -keyout data/private/tls/key.pem \
     -out data/private/tls/cert.pem \
     -subj "/CN=magicalgirl-web-oracle" \
     -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"
   chmod 700 data/private data/private/tls
   chmod 600 data/private/tls/key.pem data/private/tls/cert.pem
   ```

3. 启动两个服务：

   ```bash
   docker compose --profile admin up -d
   ```

4. 打开 `https://127.0.0.1:28443/admin/`，用 `ORACLE_ADMIN_TOKEN` 登录。

管理监听默认只绑定主机的 `127.0.0.1`。远程访问请使用 VPN 或带认证的反向代理，不要直接暴露到公网。账户与 Key 管理见 [multi-key-setup.zh-CN.md](multi-key-setup.zh-CN.md)。

## SearXNG 与 Crawl4AI

### SearXNG

在 `settings.yml` 中开启 JSON：

```yaml
search:
  formats:
    - html
    - json
```

然后设置 `SEARXNG_URL`。供应商层会把它当作本地服务并绕过 `PROXY_URL`。

### Crawl4AI

运行 Crawl4AI Docker 镜像，然后设置 `CRAWL4AI_URL`（如部署需要 Bearer Token，再设置 `CRAWL4AI_TOKEN`）。Crawl4AI 是 JavaScript 重页面和多页抓取的首选后端。

## 远程访问

推荐把服务保持在回环或私有接口，通过 VPN（WireGuard、Tailscale 等）访问。如果必须使用反向代理：

- 在代理层终止 TLS，并转发到 `127.0.0.1:28288`。
- 在代理层要求认证，或保持 `MCP_AUTH_TOKEN` 开启。
- 不要无保护地公开 `/healthz` 或 `/admin/`。
- 尽可能限制来源地址。

## 验证

```bash
# 存活与供应商健康度
curl -fsS http://127.0.0.1:28288/healthz

# 完整诊断（容器内）
docker compose exec oracle magicalgirl-web doctor --json

# MCP initialize 冒烟测试
curl -sS -D - -o /dev/null -X POST http://127.0.0.1:28288/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
```

健康响应包含 `"status":"ok"` 和 `providers` 映射。某个供应商显示 `degraded` 表示最近有一次供应商级失败；`healthy` 表示熔断器没有连续失败。

## 升级

```bash
git pull
docker compose build
docker compose up -d
```

或使用已发布镜像：

```bash
docker compose pull
docker compose up -d --no-build
```

`data/` 卷在容器替换后保留。跨小版本升级前请阅读 [CHANGELOG](../CHANGELOG.md)。

## 备份与恢复

CLI 使用 SQLite backup API 复制账户账本：

```bash
docker compose exec oracle magicalgirl-web backup \
  --db /app/data/usage.accounts.sqlite3 \
  -o /app/data/backup/usage.accounts.sqlite3
docker compose exec oracle magicalgirl-web verify-backup \
  /app/data/backup/usage.accounts.sqlite3
```

同时备份：

- `.env`（供应商 Key 和管理令牌）；
- `data/accounts.yaml`（账户 / 池 / Key 引用，不含密钥）；
- `ORACLE_VAULT_KEY`（与账本分开保存；丢失后加密凭据无法恢复）；
- `data/private/tls/`（如果使用内置管理监听）。

恢复时停止服务、替换账本/配置、再启动。信任恢复文件前先运行 `verify-backup`。

## 排障

| 症状 | 可能原因 | 修复 |
|---|---|---|
| `docker compose` 访问不到 SearXNG/Crawl4AI | 它们在另一个 Compose 项目或宿主机上 | 使用宿主机 IP / `host.docker.internal`，或放到同一 Docker 网络 |
| `/healthz` 返回 200 但搜索全部失败 | 没有供应商 Key，也没有 SearXNG | 至少设置一个供应商 Key，或部署 SearXNG |
| MCP 客户端 401 | 已启用令牌但缺少/错误的 Header | 发送 `Authorization: Bearer <MCP_AUTH_TOKEN>` |
| MCP 客户端连接被拒绝 | `MCP_BIND_IP` 是回环，客户端在远端 | 发布到可达接口，或使用 VPN |
| 管理页启动失败 | 缺少 TLS 证书/私钥或管理令牌 | 检查 `data/private/tls/` 和 `ORACLE_ADMIN_TOKEN` |
| Crawl4AI 对某页返回 502 | 目标页被拦截或无法访问 | 这是目标级失败；路由会升级到下一个后端，不会把 Crawl4AI 标记为不健康 |
| 供应商显示 `degraded` | 最近发生供应商级失败 | 查看 `search_status` 和供应商 Key；成功调用后恢复 |

## 容器用户

镜像当前以 `root` 运行，卷权限最简单。如果安全策略要求非 root，请派生镜像：创建非特权用户、`chown /app/data`，再切换用户运行。
