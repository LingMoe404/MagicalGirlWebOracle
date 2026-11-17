# マルチアカウント / マルチキー設定

[English](multi-key-setup.md) · [简体中文](multi-key-setup.zh-CN.md) · **日本語**

既定では Oracle は `.env` にあるプロバイダーごとの単一キーを使用します。マルチアカウントモードはオプトインで、次を可能にします。

- 複数のプロバイダーアカウント（例：2 つの Google アカウント）を登録；
- 共有または独立したクォータプールをモデル化；
- 1 つのプールに複数キーを紐付け；
- 管理画面から `.env` を編集せずにキーの追加・置換・無効化・ローテーション；
- キー別・プール別の監査可能な使用量台帳。

無料枠向けの設計です。プールは現在の許容量を確認するまで**利用不可**で、不明な予算は楽観的に消費せずスキップします。

## データモデル

```text
アカウントラベル（例：Google-A）
  └─ プロバイダーアカウント / 組織（例：Tavily-A）
      ├─ クォータプール（月次無料枠、1 回限りの特典など）
      └─ API キー（1 つ以上。呼び出しは特定のキーに帰属）
```

- 同じプロバイダーアカウントのキーは既定で 1 つのプールを共有します。
- プロバイダーが実際に独立したクォータを提供する場合のみプールを分けます。
- 単位の異なる残高は合算しません。USD、credits、tokens、requests を別々に追跡します。
- Jina Reader/Reranker の各キーは独立したウォレットとして扱い、キー間で共有できません。

## マルチアカウントモードを有効化

1. サンプルから `data/accounts.yaml` を作成：

   ```bash
   cp config/accounts.example.yaml data/accounts.yaml
   ```

2. ファイルを指定：

   ```ini
   ORACLE_ACCOUNTS_FILE=/app/data/accounts.yaml
   ```

3. サービスを再起動。

YAML には参照のみを書き、実際のキーは書きません。キーの `secret_env` は秘密情報を保持する環境変数名を指します。

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

その他の例：

- `config/accounts.example.yaml` — 2 つの Tavily アカウント、独立プール。
- `config/accounts.you.example.yaml` — 2 つの You.com USD ウォレット。
- `config/accounts.jina.example.yaml` — 2 つの独立 Jina トークンウォレット。

### プールを確認する

`limit: null`、`initial_used: null`、または `free_allowance_confirmed: false` のプールは消費できません。有効化前に**現在の**無料枠、既使用量、請求日、タイムゾーンを確認してください。管理画面での確認を推奨します。YAML を編集した場合は再起動が必要です。

確認後、台帳は各呼び出しの前に予算を予約し、完了後に精算します。不明または未確認の予算はスキップされます。

## 秘密情報の来源：環境変数 vs 暗号化台帳

2 つの方法があります。

1. **環境変数** — YAML の `secret_env` が環境変数名を指します。簡単ですが秘密情報は `.env` に残ります。
2. **管理画面 + 暗号化台帳** — `ORACLE_VAULT_KEY` を設定し、Web 管理画面からキーを追加します。秘密情報は Fernet 暗号文として SQLite 台帳に保存され、マスターキーは環境変数にのみ存在します。別途バックアップが必要です。

管理画面は送信後に完全なキーを返しません。応答、HTML、フロントエンド成果物、ログにはマスク済み識別子のみが含まれます。

> `ORACLE_VAULT_KEY` を失うと暗号化認証情報は復元できません。暗号化台帳が存在する間は再生成しないでください。

## 管理画面

設定：

```ini
ORACLE_ADMIN_TOKEN=<長いランダムトークン>
ORACLE_VAULT_KEY=<fernet-key>
ORACLE_ACCOUNTS_FILE=/app/data/accounts.yaml
```

管理トークンは `MCP_AUTH_TOKEN` と異なる値にする必要があります。任意の HTTPS リスナーを起動：

```bash
docker compose --profile admin up -d
```

`https://127.0.0.1:28443/admin/` を開きます。証明書設定は [deployment.ja.md](deployment.ja.md#任意の管理画面) を参照してください。

管理画面の機能：

- **クォータ概要** — アカウント/キー別の残予算、単位、請求期間、公式値かローカル推定か。
- **キー管理** — 既存の共有プールまたは新規アカウントへのキー追加、名前変更、無効化、認証情報の置換。初期使用量の確認は明示的。
- **呼び出し履歴とトレース** — プロバイダー、キー、アカウント、状態、レイテンシ、予約/精算コスト、候補順、切替理由。
- **残高スナップショット** — アカウント別の公式残高照会。失敗/期限切れスナップショットは保持され、0 として表示されません。
- **アラート** — 低残高、認証情報の無効化、連続失敗、保守的な枯渇予測。
- **キャッシュとスケジューリング** — 現在の戦略、TTL、キャッシュ件数。

## 公式残高同期

マルチアカウントを有効にすると、バックグラウンドスケジューラが Tavily、Firecrawl、You.com、Jina の公式残高を照会します。間隔は `ORACLE_BALANCE_SYNC_INTERVAL`（秒。`0` で無効、それ以外は 60–480）で設定します。

- Exa には公開残高 API がなく、応答の `costDollars` からのローカル推定のみです。
- Firecrawl / You.com の公式総額は無料/有料来源に分割できない場合があり、上限としてのみ扱います。無料予算を自動的に解放しません。
- 同期失敗時は前回スナップショットを保持して期限切れとマークし、満額に戻しません。

## キャッシュ、スケジューリング、エクスポート、バックアップ

```bash
# 検索キャッシュ 300 秒、取得キャッシュ 3600 秒
ORACLE_CACHE_TTL_SECONDS=300
ORACLE_FETCH_CACHE_TTL_SECONDS=3600

# priority | balanced | expiry | preferred_account
ORACLE_SCHEDULING_STRATEGY=balanced

# マスク済み履歴のエクスポート（CSV または JSON）
docker compose exec oracle magicalgirl-web export --month 2026-11 --format csv

# オンラインバックアップ + 検証
docker compose exec oracle magicalgirl-web backup \
  --db /app/data/usage.accounts.sqlite3 \
  -o /app/data/backup/usage.accounts.sqlite3
docker compose exec oracle magicalgirl-web verify-backup \
  /app/data/backup/usage.accounts.sqlite3
```

エクスポートにはクエリ本文、平文 URL、認証情報、フィンガープリント、マスターキーは含まれません。キャッシュヒットはプロバイダーを呼び出さず、クォータも消費しません。

## 失敗時の動作

- **プロバイダー級の失敗**（タイムアウト、認証、レート制限、サーバーエラー）はプロバイダーのヘルスサーキットに記録され、キーが短時間クールダウンすることがあります。
- **対象級の失敗**（死んだ URL、対象の 404/422、アンチボット壁）は `kind="target"` として扱われ、プロバイダーのヘルスを下げず、キーもクールダウンしません。ルーターは次の取得バックエンドへエスカレーションします。
- **非同期クロール作成**の結果が不明な場合、重複ジョブと二重課金を避けるため盲目的に再試行しません。
- 送信済みで結果不明のリクエストは台帳で `uncertain` のまま残り、予算が暗黙に解放されることはありません。

## バックアップと復元

バックアップ対象：

- 台帳（`data/usage.accounts.sqlite3`）；
- `data/accounts.yaml`；
- `.env`（プロバイダーキーと管理トークン）；
- `ORACLE_VAULT_KEY`（台帳とは別）；
- `data/private/tls/`（内蔵 HTTPS 管理リスナーを使う場合）。

復元時はサービスを停止し、`verify-backup` で復元台帳を検証してから信頼してください。[deployment.ja.md](deployment.ja.md#バックアップと復元) を参照。

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| 管理画面でキー管理が無効 | `ORACLE_VAULT_KEY` が空 | 設定して再起動。値を安定させる |
| 管理画面にアクセスできない | 管理トークンが空、または TLS ファイルがない | `ORACLE_ADMIN_TOKEN` を設定し、証明書/鍵を用意して `admin` プロファイルを起動 |
| プールが消費されない | 無料枠が未確認 | 上限、初期使用量、請求期間を確認 |
| キーがスキップされる | 無効、秘密情報なし、単位不一致、クールダウン中 | 呼び出しトレースと UI のキー状態を確認 |
| Jina キーが拒否される | Jina ウォレットはキー間で共有できない | Jina キーごとに独立したアカウント/ウォレットを作成 |
| ローカル残高が公式より低い | ローカル台帳に進行中または不確定の呼び出しが含まれる | 意図的な保守動作。残高履歴ページで照合 |
