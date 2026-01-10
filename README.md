# Asteria Case Summarizer

Asteria（OEM先）の過去ケースをbugtrackシステムからエクスポートしたHTMLファイルを解析し、Salesforceケースと同様の要約処理を行ってSQLite要約DBに統合するシステム。

## 概要

- **データソース**: bugtrack HTMLエクスポート（asteria_202501.htm）
- **出力先**: SQLite要約DB（case_summaries.db）への統合
- **ベース**: salesforce-case-summarizer を継承・拡張

## 主要機能

1. **HTMLパーサー**: bugtrack HTMLからチケットデータを抽出
2. **データ変換**: Timeline → EmailMessage 形式に変換
3. **要約生成**: LLMによる構造化要約（Salesforce版と共通）
4. **DB統合**: sourceカラムでSalesforce/Asteriaを区別して統合

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
python -m src.html_parser ../asteria/asteria_202501.htm
```

### 単体要約テスト

```bash
python -m src.asteria_summarizer --ticket 853689
```

### バッチ処理（dry-run）

```bash
python -m src.batch_processor \
    --html ../asteria/asteria_202501.htm \
    --db ~/.claude/skills/_shared/case_summaries.db \
    --dry-run
```

### バッチ処理（本番）

```bash
python -m src.batch_processor \
    --html ../asteria/asteria_202501.htm \
    --db ~/.claude/skills/_shared/case_summaries.db
```

## 依存関係

- Python 3.10+
- beautifulsoup4>=4.12.0
- lxml>=5.0.0
- pyyaml>=6.0

## 関連プロジェクト

- [salesforce-case-summarizer](../salesforce-case-summarizer) - ベースとなる要約システム
- [case_search.py](../.claude/skills/_shared/case_search.py) - FTS5検索CLI

## 詳細設計

詳細な設計・実装計画は [PLAN.md](PLAN.md) を参照してください。
