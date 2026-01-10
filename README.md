# Asteria Case Summarizer

Asteria（OEM先）の過去ケースをbugtrackシステムからエクスポートしたHTMLファイルを解析し、構造化された要約を生成してSQLite要約DBに統合するシステム。

## 概要

- **データソース**: bugtrack HTMLエクスポート（asteria_202501.htm）
- **出力先**: SQLite要約DB（case_summaries.db）への統合
- **ケース番号形式**: `AST-853689`（プレフィックスでSalesforceケースと衝突回避）

## 主要機能

1. **HTMLパーサー**: bugtrack HTMLからチケットデータを抽出（[html_parser.py](src/html_parser.py)）
2. **データ変換**: Timeline → EmailMessage 形式に変換（[asteria_fetcher.py](src/asteria_fetcher.py)）
3. **要約生成**: LLMによる構造化要約（[asteria_summarizer.py](src/asteria_summarizer.py)）
4. **DB統合**: 既存DBスキーマに統合（[batch_processor.py](src/batch_processor.py)）

## プロジェクト構成

```
asteria-case-summarizer/
├── src/
│   ├── __init__.py
│   ├── html_parser.py          # HTML解析
│   ├── asteria_fetcher.py      # EmailMessage変換
│   ├── asteria_summarizer.py   # 要約処理
│   └── batch_processor.py      # 一括処理
├── prompts/
│   └── asteria_summary_template.txt
├── config/
│   └── settings.yaml
├── output/                     # 要約結果CSV
├── data/                       # 元HTMLファイル
├── requirements.txt
├── PLAN.md                     # 詳細設計計画
└── README.md
```

## インストール

```bash
cd C:\support\asteria-case-summarizer
pip install -r requirements.txt
```

## 使用方法

### HTML解析テスト

```bash
python -m src.html_parser C:\support\asteria\asteria_202501.htm
```

出力例：
```
Found 12 tickets:
  853689: 【お問い合わせ対応】MSTeams 送信メッセージサイズの件について #25696...
  853698: 【お問い合わせ対応】[Office365Get] D365 for Salesアダプタのログ削除について...
  ...
```

### 単体要約テスト

```bash
python -m src.asteria_summarizer --ticket 853689
```

### バッチ処理（dry-run）

```bash
python -m src.batch_processor \
    --html C:\support\asteria\asteria_202501.htm \
    --db /path/to/case_summaries.db \
    --dry-run
```

### バッチ処理（本番）

```bash
python -m src.batch_processor \
    --html C:\support\asteria\asteria_202501.htm \
    --db /path/to/case_summaries.db
```

### CLIオプション

| オプション | 説明 | 必須 |
|-----------|------|------|
| `--html` | bugtrack HTMLエクスポートファイルのパス | ✓ |
| `--db` | SQLite要約DBのパス | ✓ |
| `--limit` | 処理するチケット数の上限 | - |
| `--dry-run` | ドライランモード（DB書き込みなし） | - |

## DBスキーマ

既存のSQLite要約DBにAsteriaケースを統合します：

```sql
-- summariesテーブルにAsteriaケースを追加
-- case_number: "AST-853689"（プレフィックスで衝突回避）
-- summary_text: AI生成の要約テキスト
-- key_technical_terms: メタデータJSON（source="asteria"を含む）

-- FTS検索は既存のtrigger（summaries_ai）で自動同期
```

### 既存Salesforceケースとの統合

| 項目 | Salesforceケース | Asteriaケース |
|------|------------------|---------------|
| case_number | `0006XXXX` (8桁) | `AST-853XXX` (プレフィックス付き) |
| source | `salesforce` | `asteria` |
| データソース | Salesforce API | bugtrack HTML |

## 設定

[config/settings.yaml](config/settings.yaml) で動作を設定：

```yaml
llm:
  provider: "claude"  # claude / gemini
  model: "claude-sonnet-4-20250514"
  temperature: 0.3

summarization:
  prompt_template: "prompts/asteria_summary_template.txt"

poc:
  dry_run: false  # trueの場合、DB書き込みをスキップ
```

## 要約フォーマット

生成される要約は以下の構造を持ちます：

```
## 【AI要約】

### ケース番号: 853689
### タイトル: 【お問い合わせ対応】MSTeams 送信メッセージサイズの件について

## Symptoms（現象）
...

## Environment（環境）
...

## Error codes
...

## Customer ask（顧客要望）
...

## Our actions（対応内容）
...

## Outcome（結果）
...

## Next step
...
```

## 依存関係

- Python 3.10+
- beautifulsoup4>=4.12.0
- lxml>=5.0.0
- pyyaml>=6.0
- anthropic>=0.18.0

## 実行結果

2025年1月のAsteriaケース12件を処理した結果：

```
=== Batch Processing Results ===
Total tickets: 12
Success: 11
Failed: 0
Skipped: 1
```

- **成功**: 11件（新規追加）
- **スキップ**: 1件（既存レコード）
- **失敗**: 0件
- **DB統合**: 22件（10件Salesforce + 12件Asteria）
- **FTS検索**: 正常に動作（`MATCH 'D365'` でAST-853698がヒット）

## 開発

```bash
# テスト実行
python -m src.html_parser C:\support\asteria\asteria_202501.htm 853689

# コミット
git add .
git commit -m "Description"
git push origin main
```

## ライセンス

内部ツールとして使用。

## 関連プロジェクト

- [salesforce-case-summarizer](../salesforce-case-summarizer) - Salesforceケース要約システム

## 詳細設計

詳細な設計・実装計画は [PLAN.md](PLAN.md) を参照してください。
