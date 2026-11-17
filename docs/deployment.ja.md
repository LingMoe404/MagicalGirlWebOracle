# デプロイ

[English](deployment.md) · [简体中文](deployment.zh-CN.md) · **日本語**

このガイドは Docker デプロイを扱います。Python で直接実行することもできます（環境変数は [configuration.ja.md](configuration.ja.md) を参照）。

## 前提条件

- Docker Engine と Compose プラグイン（または Docker Desktop）。
- 任意だが推奨：JSON 出力を有効にした SearXNG、および/または Crawl4AI。なくても動作しますが、セルフホスト検索とブラウザ級取得のフォールバックを失います。
- 少なくとも 1 つのプロバイダーキー、またはキーなしの You.com / Jina Reader へのアクセス。

## クイックスタート（Docker Compose）

```bash
git clone https://github.com/LingMoe404/MagicalGirlWebOracle.git
cd MagicalGirlWebOracle
cp .env.example .env
```

`.env` を編集します。

- 他のマシンから接続する場合は、十分長いランダムな `MCP_AUTH_TOKEN` を設定。
- SearXNG / Crawl4AI を動かしている場合は `SEARXNG_URL` / `CRAWL4AI_URL` を設定。
- 所有しているプロバイダーキーを設定。

起動：

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:28288/healthz
```

ポートは既定で `127.0.0.1` に公開されます。信頼できる LAN や VPN の他マシンから使う場合は、`MCP_BIND_IP` を該当インターフェース（`0.0.0.0` や VPN アドレス）に変更し、`MCP_AUTH_TOKEN` を有効に保ってください。

### 公開済みイメージを使う

```bash
# 先に .env を作成
ORACLE_IMAGE=lingmoe404/magicalgirl-web-oracle:0.1.0 \
  docker compose up -d --no-build
```

`:latest` タグもあります。本番ではバージョンを固定してください。

### ワンコマンドスクリプト

`deploy.sh` はイメージ構築、コンテナ内からの SearXNG/Crawl4AI 自動検出、初回トークン生成、起動、`magicalgirl-web doctor --json` を実行します。

```bash
bash deploy.sh
```

冪等で、既存の `.env` 値は保持します。ネットワークファイルシステムでは実行ビットが失われることがあるため `bash` 経由で呼び出します。

## 単体 Docker

stdio（単一マシン、エージェントがコンテナを起動）：

```bash
docker run --rm -i \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  lingmoe404/magicalgirl-web-oracle:0.1.0
```

HTTP（共有サービス）：

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

`data/` ボリュームには使用量台帳、任意のキャッシュ、管理画面用の暗号化アカウント状態と TLS ファイルが入ります。バックアップしてください。

## MCP クライアント設定

サービスは `/mcp` に streamable-http を公開します。汎用クライアント設定：

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

`MCP_AUTH_TOKEN` が空なら `headers` を省略します。stdio クライアントは `magicalgirl-web` または `python -m magicalgirl_web_oracle.mcp_server.server` をコマンドにします。コピー用サンプルは [`examples/mcp/`](../examples/mcp/) にあります。

## 任意の管理画面

管理画面は独立した任意の HTTPS リスナーで、MCP プロセスと使用量台帳を共有し、`/mcp` は公開しません。

1. `.env` に設定：

   ```ini
   ORACLE_ACCOUNTS_FILE=/app/data/accounts.yaml
   ORACLE_ADMIN_TOKEN=<長いランダムトークン>
   ORACLE_VAULT_KEY=<fernet-key>
   ORACLE_ADMIN_BIND_IP=127.0.0.1
   ORACLE_ADMIN_HTTPS_PORT=28443
   ```

   Fernet マスターキーの生成：

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. 自己署名証明書を生成（または独自証明書を使用）：

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

3. 両サービスを起動：

   ```bash
   docker compose --profile admin up -d
   ```

4. `https://127.0.0.1:28443/admin/` を開き、`ORACLE_ADMIN_TOKEN` でログイン。

管理リスナーは既定でホストの `127.0.0.1` にバインドします。リモートアクセスには VPN または認証付きリバースプロキシを使い、インターネットへ直接公開しないでください。アカウントとキー管理は [multi-key-setup.ja.md](multi-key-setup.ja.md) を参照してください。

## SearXNG と Crawl4AI

### SearXNG

`settings.yml` で JSON を有効にします。

```yaml
search:
  formats:
    - html
    - json
```

その後 `SEARXNG_URL` を設定します。プロバイダー層はこれをローカルサービスとして扱い、`PROXY_URL` を迂回します。

### Crawl4AI

Crawl4AI の Docker イメージを実行し、`CRAWL4AI_URL` を設定します（デプロイに Bearer トークンが必要なら `CRAWL4AI_TOKEN` も設定）。Crawl4AI は JavaScript の多いページと複数ページクロールの優先バックエンドです。

## リモートアクセス

サービスはループバックまたはプライベートインターフェースに置き、VPN（WireGuard、Tailscale など）経由で接続することを推奨します。リバースプロキシを使う場合：

- プロキシで TLS を終端し、`127.0.0.1:28288` へ転送。
- プロキシで認証を要求するか、`MCP_AUTH_TOKEN` を有効に保つ。
- `/healthz` や `/admin/` を無保護で公開しない。
- 可能なら送信元アドレスを制限。

## 検証

```bash
# 存活とプロバイダーヘルス
curl -fsS http://127.0.0.1:28288/healthz

# 詳細診断（コンテナ内）
docker compose exec oracle magicalgirl-web doctor --json

# MCP initialize スモークテスト
curl -sS -D - -o /dev/null -X POST http://127.0.0.1:28288/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
```

正常な応答には `"status":"ok"` と `providers` マップが含まれます。プロバイダーが `degraded` の場合は直近にプロバイダー級の失敗があったことを示し、`healthy` はサーキットブレーカーに連続失敗がないことを示します。

## アップグレード

```bash
git pull
docker compose build
docker compose up -d
```

または公開イメージを使用：

```bash
docker compose pull
docker compose up -d --no-build
```

`data/` ボリュームはコンテナ置換後も保持されます。マイナーバージョン間のアップグレード前に [CHANGELOG](../CHANGELOG.md) を確認してください。

## バックアップと復元

CLI は SQLite backup API でアカウント台帳をコピーします。

```bash
docker compose exec oracle magicalgirl-web backup \
  --db /app/data/usage.accounts.sqlite3 \
  -o /app/data/backup/usage.accounts.sqlite3
docker compose exec oracle magicalgirl-web verify-backup \
  /app/data/backup/usage.accounts.sqlite3
```

あわせてバックアップ：

- `.env`（プロバイダーキーと管理トークン）；
- `data/accounts.yaml`（アカウント / プール / キー参照。秘密情報なし）；
- `ORACLE_VAULT_KEY`（台帳とは別に保管。失うと暗号化認証情報を復元不能）；
- `data/private/tls/`（内蔵管理リスナーを使う場合）。

復元はサービスを停止し、台帳/設定を置換して起動します。復元ファイルを信頼する前に `verify-backup` を実行してください。

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `docker compose` から SearXNG/Crawl4AI に届かない | 別 Compose プロジェクトまたはホスト上にある | ホスト IP / `host.docker.internal` を使う、または同じ Docker ネットワークに入れる |
| `/healthz` は 200 だが検索がすべて失敗 | プロバイダーキーも SearXNG もない | 少なくとも 1 つのキーを設定するか SearXNG をデプロイ |
| MCP クライアントが 401 | トークン有効だがヘッダーがない/誤り | `Authorization: Bearer <MCP_AUTH_TOKEN>` を送る |
| MCP クライアントが接続拒否 | `MCP_BIND_IP` がループバックでクライアントが遠隔 | 到達可能なインターフェースに公開するか VPN を使う |
| 管理画面が起動しない | TLS 証明書/鍵または管理トークンがない | `data/private/tls/` と `ORACLE_ADMIN_TOKEN` を確認 |
| Crawl4AI がページに 502 | 対象ページがブロックまたは到達不能 | 対象級の失敗。ルーターは次のバックエンドへエスカレーションし、Crawl4AI を不健康にしない |
| プロバイダーが `degraded` | 直近のプロバイダー級失敗 | `search_status` とキーを確認。成功呼び出しで回復 |

## コンテナユーザー

イメージは現在 `root` で動作し、ボリューム権限が最も簡単です。非 root が必要な場合は、非特権ユーザーを作成し、`/app/data` の所有者を変更してからユーザーを切り替える派生イメージを作成してください。
