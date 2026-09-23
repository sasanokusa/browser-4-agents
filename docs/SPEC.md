# browsr-4-agent 仕様書 v1.1

AIエージェントに Web 検索とページ閲覧の機能を提供するブラウザサーバー。有料の検索 API は使わない。実装の詳細は `DESIGN.md` に記載する。

## 1. 要件

| ID | 要件 |
|---|---|
| R1 | ページの取得には Firefox(Playwright の Firefox、または Camoufox)を使う |
| R2 | 応答は JSON で返す。本文は Markdown の文字列として JSON に入れる |
| R3 | ツール定義は 2 ツール(各パラメータ 1 個)で、合計 約100 トークン以内に収める |
| R4 | 引数名の揺れや形式の誤りはサーバー側で解釈して受け入れる。検索結果やリンクは番号で指定できる |
| R5 | 1 回の応答は既定で 3,000 トークン以内に収める。超えた分は分割し、番号で続きを取得できるようにする |
| R6 | 検索バックエンドを差し替えられるようにし、失敗したら次のバックエンドへ自動で切り替える。既定はセルフホストの SearXNG とする |
| R7 | ページの取得に失敗したら、別の取得方法で再試行する。サイトに専用の取得方法があればそれを優先する |
| R8 | 分割したページは、直前の検索語に最も関係する部分から返す |

## 2. スコープ

- **対象**:Web 検索(`search`)と、ページ本文の取得(`open`)。対応する形式は HTML、PDF、プレーンテキスト、JSON。ほかにキャッシュ、レート制御、フォールバックを含む。
- **対象外(v1)**:クリック、フォーム入力、ログイン、スクリーンショット、ファイルのダウンロード。

## 3. ツール定義

### 3.1 標準モード(`mode = "standard"`、既定)

```json
[
  {"name":"search","description":"Search the web. Returns numbered results.",
   "parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}},
  {"name":"open","description":"Open a URL or a number from results/links. Returns page text.",
   "parameters":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}}
]
```

### 3.2 単一ツールモード(`mode = "single"`)

```json
{"name":"web","description":"Search words, or open a URL/number.",
 "parameters":{"type":"object","properties":{"input":{"type":"string"}},"required":["input"]}}
```

`input` が URL か番号なら `open`、それ以外なら `search` として処理する。

### 3.3 入力の解釈

| 入力 | 解釈 |
|---|---|
| 引数名が `q` / `keyword` / `text` / `input` | `query` |
| 引数名が `link` / `href` / `id` / `ref` | `url` |
| 未知の引数名で、値が文字列 1 個だけ | その値を使う |
| `example.com/a` のようにスキームがない | 先頭に `https://` を補う |
| `3`(数値)/ `"#3"` / `"[3]"` / `"(3)"` | 番号 3 |
| 前後の空白、引用符(`"'` と `` ` ``、`「」『』`) | 取り除く |
| `url` の末尾の `.,。、` | 取り除く |
| 値が配列 | 先頭の要素を使う |
| 引数全体が JSON 文字列 | パースして使う |
| `search` に URL か番号が渡された | `open` として処理する |
| `open` に URL でも番号でもない文字列が渡された | `search` として処理する |

## 4. 応答フォーマット

- JSON はインデントなし、区切りに余分な空白を入れない形で出力し、非 ASCII 文字はエスケープしない。
- ツールの結果はつねに正常な結果として返し、エラーも `error` フィールドで表す。

### 4.1 search

```json
{"query":"firefox webdriver bidi","results":[{"id":1,"title":"WebDriver BiDi - MDN","url":"https://developer.mozilla.org/...","date":"2026-08-14","snippet":"WebDriver BiDi is a ..."}],"tip":"Snippets may be outdated. open(1) for details."}
```

- 結果は既定で 8 件。snippet は最大 200 文字。
- `date` は、検索バックエンドが公開日を返したときだけ付ける(`YYYY-MM-DD`)。
- 結果が 0 件のときは `results:[]` とし、`tip` は `"No results. Try different words."` にする。
- `tip` は設定 `tools.tip = false` で出力しない。

### 4.2 open

```json
{"id":1,"title":"WebDriver BiDi - MDN","url":"https://developer.mozilla.org/...","text":"# WebDriver BiDi\n\n... [仕様書](14) ...","part":"1/3","next":17}
```

| キー | 内容 |
|---|---|
| `id` | このページの番号 |
| `url` | リダイレクト後の最終 URL |
| `text` | 本文(Markdown)。画像、ナビゲーション、ヘッダー、フッター、広告、非表示要素は除く |
| リンク | 既定は `[テキスト](番号)`。`tools.link_style = "url"` のときは `[テキスト](URL)` |
| `part` / `next` | 本文を分割したときだけ付く。`next` は、このセッションでまだ返していない部分の番号(文書の順で、返した部分より後ろを優先)。すべて返したら付かない |
| 返す部分 | ページを番号か URL で開いたときは、直前の検索語に最も関係する部分を返す。検索語がないか、どの部分にも一致しないときは 1 番目の部分を返す。`next` の番号で開いたときは、その部分を返す |
| ページ内リンク | リンク先がそのページ自身(フラグメントだけが違う URL を含む)のリンクは、テキストだけを残す |
| PDF | ページごとに `### Page n` という見出しを付ける |

### 4.3 エラー

```json
{"error":"timeout","hint":"Page too slow. Try another result, e.g. open(2)."}
```

| error | hint |
|---|---|
| `bad_input` | Pass a URL, a result number, or search words. |
| `unknown_id` | Number not found. search again or pass a URL. |
| `timeout` | Page too slow. Try another result{, e.g. open(n)}. |
| `blocked` | Site blocked access. Try another result{, e.g. open(n)}. |
| `fetch_failed` | Could not load page. Try another result{, e.g. open(n)}. |
| `not_found` | Page not found (HTTP {status}). Try another result{, e.g. open(n)}. |
| `unsupported` | Cannot read this file type. Try another result{, e.g. open(n)}. |
| `forbidden_target` | This address is not allowed. |
| `disallowed` | Site disallows robots. Try another result{, e.g. open(n)}. |
| `search_unavailable` | Search is temporarily unavailable. Try again later or open a known URL. |

`{, e.g. open(n)}` の部分は、直前の検索結果のうちまだ開いていない最初の番号があるときだけ付ける。

- `blocked`:サイトが CAPTCHA、bot 判定、アクセス拒否のページを返した。
- `fetch_failed`:通信エラー、ブラウザのエラー、内部エラー。

## 5. 番号

- 番号はセッション内で 1 から始まる通し番号で、検索結果、本文中のリンク、分割された本文の続きで共通に使う。
- 同じセッションでは、同じ URL(正規化したもの)にはつねに同じ番号を振る。
- セッションの単位は、MCP では接続、REST では `X-Session` ヘッダーの値(ヘッダーがなければ `default`)。最後のアクセスから 1 時間で失効する。

## 6. 検索バックエンド

- 問い合わせる順序は設定 `search.backends` で決める。既定は `["searxng", "ddg", "mojeek"]`。
- **SearXNG**:JSON API を使う。`settings.yml` の `search.formats` に `json` が必要。
- **DuckDuckGo(HTML 版)と Mojeek**:Firefox でページを開き、結果を抜き出す。
- 次の場合は次のバックエンドへ切り替え、失敗したバックエンドは既定で 600 秒休止する:ブロックされた(CAPTCHA、HTTP 403/429)、またはタイムアウトが 2 回続いた。
- Google を直接スクレイピングするバックエンドは実装しない。

## 7. ページの取得

- 取得方法は次の順に試す。前の方法が `blocked` または `fetch_failed` で失敗したときだけ次に進む。
  1. サイト別の取得方法(対象の URL のとき)
  2. `browser.engine` のブラウザ
  3. HTTP での直接取得
  4. Camoufox(インストールされていて、`browser.engine` が `camoufox` でないとき)
- 使う方法と順序は設定 `fetch.fallbacks` で決める。既定は `["http", "camoufox"]`。
- 手順 2 で `blocked` になったドメインは既定で 3,600 秒記録し、その間は手順 2 を飛ばす。
- すべての方法で失敗したときは、最後の方法のエラーを返す。
- サイト別の取得方法:

| サイト | 対象の URL | 取得先 |
|---|---|---|
| PyPI | `https://pypi.org/project/<name>/` と `https://pypi.org/project/<name>/<version>/` | `https://pypi.org/pypi/<name>/json`(バージョン指定時は `/pypi/<name>/<version>/json`) |

## 8. インターフェース

- **MCP サーバー**:stdio と Streamable HTTP に対応する。
- **REST**:`GET /search`、`GET /open`、`POST /call`、`GET /tools`、`GET /health`。
- **CLI**:`browsr serve | tools | search | open | doctor`。
- **ツール定義の出力**:`openai` / `anthropic` / `mcp` の各形式で出力できる。
- **配布**:`uvx browsr-4-agent` と、SearXNG を同梱した `docker compose`。

## 9. セキュリティ

- ページの本文は `text` フィールドの中だけに入れる。
- localhost やプライベート IP など、公開されていないアドレスへのアクセスは既定で拒否する(`security.allow_private = false`)。
- ファイルのダウンロードは無効にする。
- 同じドメインへのリクエストは既定で 1 秒に 1 回までにする。robots.txt に従うかは設定で選べる(既定は従わない)。
- ツールの呼び出しはすべて JSON Lines 形式でログに記録する。

## 10. 性能目標

| 項目 | 目標 |
|---|---|
| search(キャッシュなし) | p50 2 秒以内 |
| search(キャッシュあり) | 50 ミリ秒以内 |
| open | p50 4 秒以内、p95 12 秒以内 |
| 常駐メモリ | タブ 4 つで 1GB 以内 |

## 11. 評価

- **タスクセット**:50 問(事実確認、最新情報、複数ページの比較。日本語と英語)。
- **対象モデル**:4B〜8B 級のオープンモデル数種と、比較用の大型モデル 1 種。
- **指標**:
  - 有効なツールコールの割合(入力の解釈で補正する前と後)
  - ツール選択の正しさ
  - タスクの成功率
  - 1 タスクあたりの合計トークン数、1 回の応答の平均トークン数
  - 遅延(p50 / p95)
- **採点**:回答の正しさ(`check`)と出典(`cite`)を別々に判定する。成功は両方を満たしたとき。
- **ベースライン**:同じモデルをツールなしで実行し、成功率の差を Browsr の効果とする。
- **試行回数**:各条件を 3 回実行し、平均と最小・最大を示す。
- **追加の指標**:
  - 呼び出し形式の失敗(ツール呼び出しが本文に書かれた回答)の数
  - 本文を取得できた open の割合(`ok` のうち CAPTCHA などを除いたもの)と、取得方法ごとの内訳
  - open を 1 回以上使ったタスクの割合
- **比較するもの**(上から順に実行する):
  1. ツールなしと標準モード
  2. 標準モードと単一ツールモード
  3. `tip` あり(既定)と `tip` なし
  4. 検索結果 5 件と 8 件
  5. description の長さ違い 3 種
  6. `max_tokens` を 1,000 / 3,000 / 6,000 にした場合
  7. `link_style` を `id` にした場合と `url` にした場合

## 12. 将来の拡張

- `find(text)`:開いているページの中で文字列を探し、該当箇所の周辺だけを返す。
- interactive プロファイル:`click(id)` と `type(id, text)`。
- 他サイトの専用取得方法(GitHub、npm、Wikipedia など)。

## 13. マイルストーン

| ID | 内容 |
|---|---|
| M1 | `open`(Firefox での取得、本文抽出、Markdown 化、分割、番号)と REST |
| M2 | `search`(SearXNG、DDG、Mojeek、フォールバック、キャッシュ) |
| M3 | MCP、入力の解釈、ツール定義の出力 |
| M4 | 評価基盤と、小型モデルでのベンチマーク |
| M5 | Camoufox 対応、`fetch.mode = "auto"`、docker compose |
| M6 | 取得失敗の分類(`blocked` / `fetch_failed`)と CAPTCHA の検出、再試行の順序、PyPI の専用取得 |
| M7 | 検索結果の `date` と `tip`、関係する部分から返す分割、ページ内リンクの除外 |
| M8 | 評価の拡張(採点の分離、ベースライン、3 回試行、追加の指標)と再評価 |
