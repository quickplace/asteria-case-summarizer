# Asteria Case Summarizer 運用ガイド

このドキュメントは、Asteriaケースの要約処理の日常運用手順を説明します。

## 目次
- [基本情報](#基本情報)
- [コマンドリファレンス](#コマンドリファレンス)
- [推奨運用パターン](#推奨運用パターン)
- [トラブルシューティング](#トラブルシューティング)
- [メンテナンス](#メンテナンス)

---

## 基本情報

### 前提条件
- **DBパス**: `/home/user/support/summary-backfill/data/case_summaries.db`
- **HTMLソース**: bugtrackからエクスポートしたHTMLファイル（`/mnt/c/support/asteria/`）
- **処理速度**: 約6秒/件（Gemini API使用）
- **成功率**: 98-100%（HTML構造の例外ケースで稀に失敗）

### 処理能力

| 期間 | ケース数（目安） | 処理時間 | 推奨用途 |
|------|----------------|---------|---------|
| 1ヶ月 | 12件 | 1-2分 | 定期更新 |
| 3ヶ月 | 35件 | 3-4分 | 定期更新 |
| 6ヶ月 | 70件 | 7-8分 | 定期更新（推奨） |
| 1年 | 140件 | 14-15分 | 初回一括 |

---

## コマンドリファレンス

### 基本コマンド

```bash
cd /home/user/support/asteria-case-summarizer
source .venv/bin/activate

python3 -m src.batch_processor \
  --html <HTMLファイルパス> \
  --db <DBパス> \
  [オプション]
```

### 必須パラメータ

| パラメータ | 説明 | 例 |
|-----------|------|-----|
| `--html` | bugtrackからエクスポートしたHTMLファイルのパス | `asteria_2025H1.htm` |
| `--db` | 統合SQLite DBのパス | `/home/user/support/summary-backfill/data/case_summaries.db` |

### オプションパラメータ

| パラメータ | 説明 | 用途 |
|-----------|------|------|
| `--delete-asteria` | 既存のAsteriaケース（AST-*）を全削除してから処理 | 全再処理時に使用 |
| `--limit N` | 処理するケース数をN件に制限 | テスト・検証時に使用 |
| `--dry-run` | 要約生成のみ行い、DB書き込みをスキップ | 動作確認時に使用 |
| `--overwrite` | 既存のケースを上書きする | 既存ケースの要約を再生成時に使用 |

---

## 推奨運用パターン

### パターンA: 定期的な全再処理（推奨）

**用途**: 半年ごとに全データをリフレッシュする運用

```bash
# Step 1: bugtrackから半年分のHTMLをエクスポート
# Web UI: 期間指定（例: 2025/01/01 - 2025/06/30）
# 保存先: /mnt/c/support/asteria/asteria_2025H1.htm

# Step 2: シンボリックリンク作成
cd /home/user/support/asteria-case-summarizer
ln -sf /mnt/c/support/asteria/asteria_2025H1.htm .

# Step 3: 既存データを削除して全再処理
source .venv/bin/activate
python3 -m src.batch_processor \
  --html asteria_2025H1.htm \
  --db /home/user/support/summary-backfill/data/case_summaries.db \
  --delete-asteria
```

**メリット**:
- データの一貫性が保たれる
- 処理済みケースの更新を反映できる
- 運用がシンプル

**実行タイミング**: 半年ごと（H1: 7月初旬、H2: 1月初旬）

---

### パターンB: 初回一括 + 定期差分追加

**用途**: 初回のみ大量データを取り込み、以降は差分追加

#### 初回: 過去1年分の一括取り込み

```bash
# Step 1: bugtrackから1年分のHTMLをエクスポート
# 期間: 2024/01/01 - 2024/12/31
# 保存先: /mnt/c/support/asteria/asteria_2024.htm

# Step 2: 初回一括処理（約14分）
cd /home/user/support/asteria-case-summarizer
source .venv/bin/activate
python3 -m src.batch_processor \
  --html asteria_2024.htm \
  --db /home/user/support/summary-backfill/data/case_summaries.db \
  --delete-asteria
```

#### 定期更新: 3ヶ月ごとに差分追加

```bash
# 3ヶ月分のHTMLをエクスポート（例: 2025Q1）
# 期間: 2025/01/01 - 2025/03/31
# 保存先: /mnt/c/support/asteria/asteria_2025Q1.htm

# 差分追加（--delete-asteriaなし）
python3 -m src.batch_processor \
  --html asteria_2025Q1.htm \
  --db /home/user/support/summary-backfill/data/case_summaries.db
```

**メリット**:
- 定期処理が短時間（3-4分）
- 過去データはそのまま保持

**デメリット**:
- 重複チェックが必要（スクリプトは自動でスキップ）
- 処理済みケースの更新は反映されない

---

### パターンC: 月次更新

**用途**: 最新データを常に維持したい場合

```bash
# 毎月初旬に前月分を処理
# 例: 2025年1月分（期間: 2025/01/01 - 2025/01/31）

cd /home/user/support/asteria-case-summarizer
source .venv/bin/activate
python3 -m src.batch_processor \
  --html asteria_202501.htm \
  --db /home/user/support/summary-backfill/data/case_summaries.db
```

**メリット**:
- 常に最新のデータが利用可能
- 処理時間が短い（1-2分）

**デメリット**:
- 運用頻度が高い

---

## トラブルシューティング

### 問題1: 一部のケースが失敗する

**症状**:
```
case=AST-854068 source=asteria status=failed(reason=no_summary)
```

**原因**: HTML構造が標準形式と異なるケース（例: メール履歴がない）

**対処法**:
1. 失敗ケースのIDを記録
2. バッチ処理は継続（他のケースは正常処理される）
3. 必要に応じて手動で対応

**確認コマンド**:
```bash
# DBに保存されたケース数を確認
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/user/support/summary-backfill/data/case_summaries.db')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM summaries WHERE case_number LIKE \"AST-%\"')
print(f'Asteria cases: {cur.fetchone()[0]}')
conn.close()
"
```

---

### 問題2: Gemini APIエラー

**症状**:
```
Error calling Gemini API: 429 Resource Exhausted
```

**原因**: APIレート制限超過（稀）

**対処法**:
1. 数分待ってから再実行
2. `--limit`オプションで処理件数を分割

```bash
# 30件ずつ処理
python3 -m src.batch_processor \
  --html asteria_2025H1.htm \
  --db /home/user/support/summary-backfill/data/case_summaries.db \
  --limit 30
```

---

### 問題3: HTMLファイルが見つからない

**症状**:
```
FileNotFoundError: [Errno 2] No such file or directory: 'asteria_2025H1.htm'
```

**対処法**: シンボリックリンクを作成

```bash
ln -sf /mnt/c/support/asteria/asteria_2025H1.htm .
ls -l asteria_2025H1.htm  # リンク確認
```

---

## メンテナンス

### 定期確認項目（月次）

```bash
# 1. Asteriaケース数の確認
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/user/support/summary-backfill/data/case_summaries.db')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM summaries WHERE case_number LIKE \"AST-%\"')
print(f'Total Asteria cases: {cur.fetchone()[0]}')
conn.close()
"

# 2. 要約品質の統計確認
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/user/support/summary-backfill/data/case_summaries.db')
cur = conn.cursor()
cur.execute('''
    SELECT
        AVG(length(COALESCE(symptoms,'')) + length(COALESCE(our_actions,'')) + length(COALESCE(outcome,''))) as avg_length,
        MIN(length(COALESCE(symptoms,'')) + length(COALESCE(our_actions,'')) + length(COALESCE(outcome,''))) as min_length,
        MAX(length(COALESCE(symptoms,'')) + length(COALESCE(our_actions,'')) + length(COALESCE(outcome,''))) as max_length
    FROM summaries
    WHERE case_number LIKE 'AST-%'
''')
result = cur.fetchone()
print(f'Average summary length: {result[0]:.0f} chars')
print(f'Min: {result[1]} chars, Max: {result[2]} chars')
conn.close()
"
```

### サンプル要約の確認

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/user/support/summary-backfill/data/case_summaries.db')
cur = conn.cursor()
cur.execute('''
    SELECT case_number, symptoms, our_actions, outcome
    FROM summaries
    WHERE case_number LIKE 'AST-%'
    ORDER BY case_number DESC
    LIMIT 1
''')
row = cur.fetchone()
print(f'=== Latest: {row[0]} ===')
print(f'Symptoms: {(row[1] or \"N/A\")[:200]}...')
print(f'Our Actions: {(row[2] or \"N/A\")[:200]}...')
print(f'Outcome: {(row[3] or \"N/A\")[:200]}')
conn.close()
"
```

---

## クイックリファレンス

### よく使うコマンド集

```bash
# 半年分を全再処理（最も一般的）
python3 -m src.batch_processor \
  --html asteria_2025H1.htm \
  --db /home/user/support/summary-backfill/data/case_summaries.db \
  --delete-asteria

# テスト実行（最初の5件のみ、DB書き込みなし）
python3 -m src.batch_processor \
  --html asteria_2025H1.htm \
  --db /home/user/support/summary-backfill/data/case_summaries.db \
  --limit 5 \
  --dry-run

# 差分追加（既存データ保持）
python3 -m src.batch_processor \
  --html asteria_2025Q2.htm \
  --db /home/user/support/summary-backfill/data/case_summaries.db

# 既存ケースを上書きして再処理
python3 -m src.batch_processor \
  --html asteria_2025H1.htm \
  --db /home/user/support/summary-backfill/data/case_summaries.db \
  --overwrite

# Asteriaケース数確認
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/user/support/summary-backfill/data/case_summaries.db')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM summaries WHERE case_number LIKE \"AST-%\"')
print(f'Asteria cases: {cur.fetchone()[0]}')
conn.close()
"
```

---

## 連絡先・リソース

- **GitHubリポジトリ**: https://github.com/quickplace/asteria-case-summarizer
- **実装プラン**: `IMPLEMENTATION_PLAN.md`
- **技術詳細**: `README.md`

---

**最終更新**: 2026-01-11
