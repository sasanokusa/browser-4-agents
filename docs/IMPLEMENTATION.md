# 実装と検証

`SPEC.md` の外部仕様と `DESIGN.md` の構成を基に、Python パッケージ、REST／MCP／CLI、評価ランナーを実装した。

v1.1 の M6 で追加した取得失敗の分類・再試行・PyPI 専用取得と、169件のテスト結果は [M6 の記録](M6.md) を参照。以下は主に初期実装時の補足である。

## 実装上の補足

- Firefox はリダイレクトの各段階で Playwright の route ハンドラーを呼ぶとは限らない。`fetch/proxy.py` のローカルプロキシをブラウザの接続経路に入れ、HTTP の転送先と HTTPS CONNECT の接続先を検査する。接続直前に DNS を再解決して検査した IP に接続し、禁止先への遷移とサブリソースを拒否する。通常のリダイレクトでは最終 URL と相対リンクの解決を保持する。
- MCP Python SDK 1.x のデコレーター API と 2.x のコールバック API の両方を扱う。MCP の引数オブジェクトの形式はプロトコルに従い、その内側の引数名・値は Browsr で補正する。REST `/call` では JSON 文字列の引数も受け付ける。
- 応答予算には JSON のエスケープ、メタデータ、リンクの展開分を含める。URL 表示ではリンクを展開してから分割し、番号表示では番号の最大幅を確保する。本文の分割結果は最大64件保持する。
- 同じページへの同時要求は取得処理を共有する。一つの呼び出しがキャンセルされてもほかの待機者の取得は継続する。セッション内の呼び出しは順序を保ち、処理中のセッションは保守タスクで失効させない。
- PDF 抽出は専用の単一ワーカーで順番に実行し、Browsr 内で PyMuPDF の処理が重ならないようにした。[PyMuPDF は複数スレッドによる同時利用をサポートしない](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html)ためである。外部コードが同じプロセスで PyMuPDF を直接操作する場合まで同期するものではない。
- CLI の単発呼び出しはプロセスごとにセッションを作る。番号をまたいで読む場合は常駐する REST／MCP サーバーを利用する。

## 検証コマンド

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests eval
.venv/bin/ruff format --check src tests eval
.venv/bin/python -m build
.venv/bin/browsr doctor
docker compose config --quiet
docker compose build browsr
docker compose run --rm browsr doctor
```

自動テストには、入力補正、参照番号、キャッシュ、抽出、分割、検索フォールバック、アクセス制御、REST／MCP を含めた。実 Firefox を使うテストでは、ローカルサイトのリダイレクト、PDF、同意バナー、タイムアウト、禁止先へのアクセス防止と、検索 → open → next の流れを検証する。

2026-09-23 の最終確認では107件のテストが成功し、Ruff の静的検査と整形チェックも通過した。wheel／sdist と Docker イメージをビルドし、Docker 内での PDF 抽出も確認した。

外部接続では、実 SearXNG の JSON 検索、Firefox での `https://example.com` の取得、既定設定でのプライベートアドレス拒否を確認した。Docker 内の Firefox、SearXNG、SQLite は `doctor` でも検証した。

検索バックエンドの個別疎通では SearXNG と DuckDuckGo は結果を返した。Mojeek はこの環境からの実アクセスでブロックされ、サーキットブレーカーが cooldown に移った。固定 HTML によるパーサー検証と、外部サービスが実際に応答するかの検証は区別する。

## 評価と残る検証範囲

`eval/` に50問と設定バリアントを置いた。採点用の根拠・基準日は `eval/REFERENCES.md` に記録する。モデルの回答の採点は正規表現・文字列による検査であり、出典の質や説明全体を保証するものではない。

ローカル Ollama の `gemma4:e2b` と `gemma4:e4b` で、それぞれ同一の50問を実行した。[比較レポート](../eval/RESULTS.md) に設定、合格数、トークン量、時間を記録する。`qwen3.8:27b` はユーザーの指定に従い実行していない。36通りの設定バリアントの総当たり評価は未実施である。

Camoufox はオプションとして実装済みだが、この環境では実ブラウザをインストールしていないため実機検証はしていない。公開検索サービスの可用性と、性能目標の p50／p95 は実行環境・検索先に依存する。評価結果から、実測済みの条件と範囲を確認すること。
