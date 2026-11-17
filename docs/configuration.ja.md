# 設定

[English](configuration.md) · [简体中文](configuration.zh-CN.md) · **日本語**

MagicalGirlWebOracle は 2 か所から設定を読み込みます。

1. **環境変数** — 通常はカレントディレクトリ（ソースから実行する場合はプロジェクトルート）の `.env`。
2. **YAML ファイル** — `config/providers.yaml` と `config/router_policy.yaml`。wheel からインストールした場合、ローカルの `config/` がなければ `magicalgirl_web_oracle/config/defaults/` の内蔵既定値を使用します。

実際の秘密情報は `.env`、環境変数、または暗号化されたアカウント台帳にのみ置き、リポジトリへコミットしないでください。

## 環境変数

### プロバイダーキー（すべて任意）

キーが存在するプロバイダーのみ有効になります。You.com と Jina Reader はキーなしモードにも対応しています。キーが欠けている場合は、そのプロバイダーがルーティングから外れるだけです。

| 変数 | 用途 |
|---|---|
| `EXA_API_KEY` | Exa 検索 / contents |
| `TAVILY_API_KEY` | Tavily 検索 |
| `FIRECRAWL_API_KEY` | Firecrawl 取得 / クロール（最後の手段） |
| `JINA_API_KEY` | Jina Reader と（任意の）リランク |
| `YDC_API_KEY` | You.com のキー付きプロファイル。キーなし MCP も利用可能 |

### セルフホストサービス

| 変数 | 既定値 | 用途 |
|---|---|---|
| `SEARXNG_URL` | 空 | SearXNG の URL（JSON 出力を有効にする必要あり） |
| `CRAWL4AI_URL` | 空 | Crawl4AI API の URL |
| `CRAWL4AI_TOKEN` | 空 | デプロイに必要な場合の Crawl4AI Bearer トークン |
| `PROXY_URL` | 空 | クラウドプロバイダー向けの外向きプロキシ。セルフホスト URL は自動的に迂回 |

`PROXY_URL` は `HTTPS_PROXY` / `HTTP_PROXY` にフォールバックします。`SEARXNG_URL` と `CRAWL4AI_URL` へのリクエストは常に直接接続します。

### 動作

| 変数 | 既定値 | 用途 |
|---|---|---|
| `ALLOW_PAID_OVERAGE` | `false` | ハードルール。false を維持。有料超過は非対応 |
| `SEARCH_MODE_DEFAULT` | `auto` | `fast` / `auto` / `high` / `max` |
| `USAGE_DB_PATH` | `data/usage.sqlite3` | SQLite 使用量台帳 |
| `STORE_QUERY_TEXT` | `false` | クエリ本文を保存するか（既定はソルト付きハッシュのみ） |
| `QUERY_HASH_SALT` | 内蔵フォールバック | ハッシュを長期保存する場合は独自のランダムソルトを設定 |
| `JINA_KEYLESS_FALLBACK` | `true` | Jina キーがない場合に低レートのキーなし `r.jina.ai` を許可 |

### MCP トランスポートとアクセス

| 変数 | 既定値 | 用途 |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio`、`streamable-http`、`sse` |
| `MCP_HOST` | `127.0.0.1` | 直接 HTTP 実行時のバインド先。`0.0.0.0` で外部公開 |
| `MCP_PORT` | `28288` | HTTP ポート |
| `MCP_PATH` | `/mcp` | HTTP パス |
| `MCP_BIND_IP` | `127.0.0.1` | Docker Compose が公開するホスト側インターフェース |
| `MCP_AUTH_TOKEN` | 空 | 設定するとクライアントに Bearer トークンが必要 |

共有デプロイでは十分長いランダムな `MCP_AUTH_TOKEN` を設定し、必要なインターフェース（LAN、VPN、ループバック）にのみポートを公開してください。詳細は [deployment.ja.md](deployment.ja.md) と [../SECURITY.ja.md](../SECURITY.ja.md) を参照してください。

### マルチアカウント / 管理画面（任意）

| 変数 | 既定値 | 用途 |
|---|---|---|
| `ORACLE_ACCOUNTS_FILE` | 空 | マルチアカウント YAML のパス（例：`data/accounts.yaml`） |
| `ORACLE_ADMIN_TOKEN` | 空 | `/admin/` 用の独立 Bearer トークン。空なら管理画面は無効 |
| `ORACLE_VAULT_KEY` | 空 | Web 管理認証情報用の Fernet マスターキー |
| `ORACLE_ADMIN_BIND_IP` | `127.0.0.1` | Docker Compose が管理リスナー用に公開するホスト側インターフェース |
| `ORACLE_ADMIN_HTTPS_PORT` | `28443` | 内蔵 HTTPS 管理リスナーのポート |
| `ORACLE_ADMIN_TLS_CERT` | `/app/data/private/tls/cert.pem` | TLS 証明書 |
| `ORACLE_ADMIN_TLS_KEY` | `/app/data/private/tls/key.pem` | TLS 秘密鍵 |
| `ORACLE_BALANCE_SYNC_INTERVAL` | `300` | 公式残高同期の間隔（秒）。`0` で無効、それ以外は 60–480 |
| `ORACLE_LOW_QUOTA_RATIO` | `0.1` | 低残高警告のしきい値（`0`–`1`）。`0` で無効 |

`ORACLE_ADMIN_TOKEN` は `MCP_AUTH_TOKEN` と異なる値にする必要があります。管理リスナーは `/mcp` を公開しません。

### キャッシュとスケジューリング

| 変数 | 既定値 | 用途 |
|---|---|---|
| `ORACLE_CACHE_TTL_SECONDS` | `0` | 検索キャッシュ TTL。`0` で無効 |
| `ORACLE_FETCH_CACHE_TTL_SECONDS` | 検索キャッシュ有効時は `3600`、それ以外は `0` | 取得キャッシュ TTL |
| `ORACLE_CACHE_MAX_ENTRIES` | `500` | 最大キャッシュ件数（10–10000） |
| `ORACLE_CACHE_PATH` | 使用量台帳と同じディレクトリ | 独立した SQLite キャッシュファイル |
| `ORACLE_SCHEDULING_STRATEGY` | `priority` | `priority`、`balanced`、`expiry`、`preferred_account` |
| `ORACLE_PREFERRED_ACCOUNT` | 空 | `preferred_account` の場合は必須 |

キャッシュヒットはプロバイダーを呼び出さず、クォータも消費しません。鮮度が重要なクエリ（`latest`、`today`、`live`、`最新`、`今天`、`实时` など）は自動的にキャッシュをスキップします。

## YAML 設定

### `config/providers.yaml`

各プロバイダーの無料クォータのメタデータ（種類、上限、単位、更新有無、コスト階層、機能、メモ）。ルーターはこれを使って候補を採点し、リクエストが無料枠内であることを証明できるか判断します。API キーは書かないでください。

### `config/router_policy.yaml`

採点の重み、モード定義（`fast`、`auto`、`high`、`max`）、中国語クエリのブースト、取得のエスカレーション順、有料超過ガード。既定値は保守的で、安価なプロバイダーとセルフホストを優先し、不明なクォータはスキップします。

## マルチアカウント設定

マルチアカウントモードはオプトインで、`ORACLE_ACCOUNTS_FILE` が指す別の YAML を使います。アカウント、クォータプール、キーを明示的に表現し、管理画面から `.env` を編集せずにキーを追加・ローテーションできます。スキーマ、Web 管理、バックアップ / 復元は [multi-key-setup.ja.md](multi-key-setup.ja.md) を参照してください。

## 設定の確認

```bash
magicalgirl-web status      # プロバイダーのヘルス、クォータ、マスク済み設定
magicalgirl-web doctor      # 接続と設定の診断
magicalgirl-web doctor --json
```

どちらのコマンドもプロバイダーキーや Vault キーを出力しません。
