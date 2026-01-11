# Asteria Case Summarizer - Implementation Plan

## Overview
Asteria（OEM先）の過去ケースをbugtrackシステムからエクスポートしたHTMLファイルを解析し、Salesforceケースと同様の要約処理を行ってSQLite要約DBに統合するシステムを開発する。

## Key Differences: Salesforce vs Asteria

| 項目 | Salesforce Case | Asteria Case |
|------|-----------------|--------------|
| データソース | PostgreSQL processed_emails / Salesforce MCP | bugtrack HTMLエクスポート |
| データ形式 | 匿名化済みメールスレッド | HTMLテーブル（チケット履歴） |
| ケース番号形式 | 0006XXXX (8桁) | 853XXX (6桁) |
| 担当者表示 | 顧客/サポート | Asteria担当者 → CData Japan Support |
| 主要フィールド | subject, status, created_date | Title, Area, Opened, Closed, Priority, Type, Details |

## Target SQLite DB Schema

既存の `case_summaries.db` に統合：

```sql
-- 正規テーブル
CREATE TABLE summaries (
    case_number TEXT PRIMARY KEY,
    summary_text TEXT NOT NULL,
    outcome TEXT,
    metadata TEXT,  -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- FTS5仮想テーブル（trigram tokenizer, 日本語対応）
CREATE VIRTUAL TABLE summaries_fts_trigram USING fts5(
    case_number,
    summary_text,
    tokenize='trigram'
);
```

## Phase 1: HTML Parser Development

### 1.1 HTML Table Extraction

**File**: `C:\support\asteria-case-summarizer\src\html_parser.py`

Extract data from `asteria_202501.htm`:

```python
from dataclasses import dataclass
from datetime import datetime
from bs4 import BeautifulSoup
from typing import List, Optional

@dataclass
class AsteriaTicket:
    """Asteriaチケットデータ"""
    ticket_id: str           # 853689
    title: str              # 【お問い合わせ対応】MSTeams 送信メッセージサイズ...
    area: str               # Drivers/Teams
    opened: datetime        # 2025/01/07 17:45
    closed: Optional[datetime]  # 2025/01/08 14:44
    priority: str           # Middle
    type: str               # Defect
    importance: str         # Middle
    linked_to: str          # 関連チケットID
    details: str            # HTML形式のやり取り履歴
    raw_html: str           # 元のHTML行
```

**Key Functions**:
- `parse_html_file(html_path: str) -> List[AsteriaTicket]`
- `clean_details_html(details_html: str) -> str` - HTML→テキスト変換（`<br>`, `<p>`, `<li>`は改行維持）
- `extract_timeline(details: str) -> List[dict]` - OPENED/ASSIGNED/EDITED/RESOLVED/CLOSEDを時系列整理

**HTML→テキスト変換の注意点**:
- `<br>`, `<p>`, `<li>` は改行に変換（ログの可読性維持）
- ログっぽいブロック（コード/スタックトレース）は整形せず温存
- LLMはHTML形式よりも改行維持されたテキストの方が読みやすい

### 1.2 Timeline Extraction

HTML内の履歴エントリを構造化：

```python
@dataclass
class TimelineEntry:
    timestamp: datetime
    action_type: str  # OPENED, ASSIGNED, EDITED, RESOLVED, CLOSED
    user: str
    content: str
```

## Phase 2: Data Conversion to EmailMessage Format

### 2.1 Adapter Class

**File**: `C:\support\asteria-case-summarizer\src\asteria_fetcher.py`

Asteriaチケットを `EmailMessage` 形式に変換：

```python
from salesforce_case_summarizer.src.fetcher import EmailMessage

class AsteriaFetcher:
    """AsteriaチケットをEmailMessage形式に変換"""

    def fetch_by_ticket_id(self, ticket_id: str) -> List[EmailMessage]:
        """
        チケットIDからメールデータを取得

        Asteriaのやり取りを顧客↔サポート形式に変換：
        - OPENED + ASSIGNED (Asteria → CData) → 【顧客→サポート】
        - EDITED/ASSIGNED (CData → Asteria) → 【サポート→顧客】
        - RESOLVED/CLOSED → 結論としてマーク
        """

    def _convert_timeline_to_emails(self, ticket: AsteriaTicket) -> List[EmailMessage]:
        """
        TimelineEntryをEmailMessageに変換

        変換ルール：
        1. `user` の値で判定
           - Asteria担当者 → is_incoming=True（顧客問合せ）
           - CData Japan Support → is_incoming=False（サポート回答）
        2. 判定できないものは `UNKNOWN` として隔離、system note扱い
        3. 複数のCData回答は結合して1メールとして扱う（簡略化）
        """
```

## Phase 3: Summarization Integration

### 3.1 AsteriaSummarizer Class

**File**: `C:\support\asteria-case-summarizer\src\asteria_summarizer.py`

```python
from salesforce_case_summarizer.src.summarizer import CaseSummarizer, CaseSummary

class AsteriaSummarizer(CaseSummarizer):
    """Asteriaケース要約クラス（Salesforce版を継承）"""

    def __init__(self, html_path: str, config_path: str = "config/settings.yaml"):
        super().__init__(config_path)
        self.html_path = html_path
        self.parser = AsteriaHTMLParser(html_path)
        self.fetcher = AsteriaFetcher()

    def process_ticket(self, ticket_id: str) -> Optional[CaseSummary]:
        """
        Asteriaチケットを要約

        Args:
            ticket_id: チケット番号（例: "853689"）

        Returns:
            CaseSummary（case_numberはチケットIDを使用）
        """
        # 1. HTMLからチケット取得
        ticket = self.parser.get_ticket_by_id(ticket_id)

        # 2. EmailMessage形式に変換
        emails = self.fetcher.convert_ticket_to_emails(ticket)

        # 3. 既存の要約ロジックを再利用
        email_thread = self.merge_emails(emails)
        summary = self.generate_summary(ticket_id, email_thread)

        return summary
```

### 3.2 Prompt Template Adjustment

**File**: `C:\support\asteria-case-summarizer\prompts\asteria_summary_template.txt`

Asteria専用プロンプト（基本はSalesforce版と共通）：

```
あなたはテクニカルサポートケースの要約を作成するアシスタントです。

以下のAsteria（OEM先）サポートケースを分析し、構造化された要約を作成してください。

## 入力
チケット番号: {ticket_id}
製品エリア: {area}
優先度: {priority}
やり取り履歴:
{timeline}

[Salesforce版と同じ出力形式]
```

## Phase 4: SQLite DB Integration

### 4.1 Database Schema Extension

**既存DBにAsteriaケースを追加**

```sql
-- summaries テーブルに source カラム追加（既存の場合）
ALTER TABLE summaries ADD COLUMN source TEXT DEFAULT 'salesforce';
-- Asteria: source='asteria', Salesforce: source='salesforce'

-- 既存レコードを更新
UPDATE summaries SET source='salesforce' WHERE source IS NULL;
```

### 4.2 Batch Processing

**File**: `C:\support\asteria-case-summarizer\src\batch_processor.py`

```python
class AsteriaBatchProcessor:
    """Asteriaケースの一括要約処理"""

    def __init__(self, html_path: str, db_path: str):
        self.parser = AsteriaHTMLParser(html_path)
        self.summarizer = AsteriaSummarizer(html_path)
        self.db_path = db_path

    def process_all(self, limit: Optional[int] = None) -> dict:
        """
        全チケットを要約してDBに登録

        Args:
            limit: 処理件数上限（テスト用）

        Returns:
            処理統計
        """
        tickets = self.parser.parse_all_tickets()

        if limit:
            tickets = tickets[:limit]

        results = {
            "total": len(tickets),
            "success": 0,
            "failed": 0,
            "skipped": 0
        }

        for ticket in tickets:
            try:
                # 既存チェック
                if self._exists_in_db(ticket.ticket_id):
                    results["skipped"] += 1
                    continue

                summary = self.summarizer.process_ticket(ticket.ticket_id)
                if summary:
                    self._save_to_db(summary, source='asteria')
                    results["success"] += 1
                else:
                    results["failed"] += 1

            except Exception as e:
                print(f"Error processing {ticket.ticket_id}: {e}")
                results["failed"] += 1

        return results

    def _save_to_db(self, summary: CaseSummary, source: str):
        """SQLite要約DBに保存"""
        import sqlite3
        import json

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        # case_numberにプレフィックスを付けて衝突回避（Asteria: AST-853689）
        case_number = f"AST-{summary.case_number}" if source == 'asteria' else summary.case_number

        cur.execute("""
            INSERT OR REPLACE INTO summaries
            (case_number, summary_text, outcome, metadata, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            case_number,
            summary.summary_text,
            summary.outcome,  # 解決内容そのもの（from_managerはmetadata内）
            json.dumps(summary.metadata, ensure_ascii=False),
            source,
            datetime.now()
        ))

        # FTSテーブルにも登録（source列を含む3列）
        cur.execute("""
            INSERT OR REPLACE INTO summaries_fts_trigram
            (case_number, summary_text, source)
            VALUES (?, ?, ?)
        """, (case_number, summary.summary_text, source))

        conn.commit()
        conn.close()
```

## Phase 5: Project Structure

```
C:\support\asteria-case-summarizer\
├── src/
│   ├── __init__.py
│   ├── html_parser.py          # HTML解析（Phase 1）
│   ├── asteria_fetcher.py      # EmailMessage変換（Phase 2）
│   ├── asteria_summarizer.py   # 要約処理（Phase 3）
│   └── batch_processor.py      # 一括処理（Phase 4）
├── prompts/
│   └── asteria_summary_template.txt  # Asteria専用プロンプト
├── config/
│   └── settings.yaml           # Salesforce版と共通
├── output/                     # 要約結果CSV
├── data/
│   └── asteria_202501.htm      # 元HTMLファイル
├── requirements.txt
└── README.md
```

## Implementation Order

1. **Phase 1**: HTMLパーサー開発
   - BeautifulSoupでのHTML解析
   - AsteriaTicketデータクラス
   - Timeline抽出機能

2. **Phase 2**: EmailMessage変換
   - AsteriaFetcherクラス
   - Timeline → EmailMessage 変換ロジック

3. **Phase 3**: 要約統合
   - AsteriaSummarizerクラス（Salesforce版を継承）
   - プロンプト調整

4. **Phase 4**: DB統合
   - バッチ処理クラス
   - SQLite登録処理
   - 既存DBスキーマの拡張（sourceカラム）

5. **Testing & Validation**
   - テスト：1〜3件のチケットで要約品質確認
   - FTS5検索でAsteriaケースが検索可能か確認
   - SalesforceケースとAsteriaケースの混在検索テスト

## Critical Files to Modify/Create

| ファイル | 操作 | 説明 |
|---------|------|------|
| `asteria-case-summarizer/src/html_parser.py` | 新規 | HTML解析 |
| `asteria-case-summarizer/src/asteria_fetcher.py` | 新規 | データ変換 |
| `asteria-case-summarizer/src/asteria_summarizer.py` | 新規 | 要約処理 |
| `asteria-case-summarizer/src/batch_processor.py` | 新規 | バッチ処理 |
| `asteria-case-summarizer/requirements.txt` | 新規 | beautifulsoup4, lxml |
| `case_summaries.db` | 変更 | ALTER TABLE ADD COLUMN source |

## Dependencies

```
beautifulsoup4>=4.12.0
lxml>=5.0.0          # HTMLパーサー
pyyaml>=6.0          # 設定ファイル（Salesforce版と共通）
anthropic>=0.18.0    # LLM API（Salesforce版と共通）
```

## Verification Steps

1. **HTML解析確認**
   ```bash
   python -m src.html_parser C:\support\asteria\asteria_202501.htm
   # → 853689, 853698,... のチケットリストが取得できるか
   ```

2. **単体要約テスト**
   ```bash
   python -m src.asteria_summarizer --ticket 853689
   # → 要約が生成されるか
   ```

3. **DB登録テスト**
   ```bash
   python -m src.batch_processor --limit 3
   # → 3件がDBに登録されるか
   ```

4. **検索確認**
   ```bash
   python ~/.claude/skills/_shared/case_search.py "MSTeams AND メッセージサイズ"
   # → Asteriaケースがヒットするか
   ```

## 追加テスト計画（守りのテスト）

| テスト | 目的 | 方法 |
|--------|------|------|
| **回帰テスト** | 既存Salesforceケースが引き続き検索できる | FTS再構築後に既存クエリを再実行 |
| **冪等性テスト** | バッチを2回回ってskippedが増える | 1回目のsuccess → 2回目は全てskipped |
| **整合性チェック** | summariesとFTSの件数一致 | 起動時またはバッチ完了後に確認 |
| **0件ヒットUX** | 検索0件時のクエリ改善ガイド | FTS5検索で0件の場合、ヒント表示 |

## DB Architecture Decision: Unified vs Separate

| 項目 | 統合DB (sourceカラム) | 別DB (asteria_summaries.db) |
|------|----------------------|----------------------------|
| **検索** | ✅ 一箇所で全ケース検索可能 | ❌ 2つのDBを跨ぐ検索が必要 |
| **管理** | ✅ シンプルな構造 | ⚠️ 2つのDBを管理 |
| **既存への影響** | ⚠️ スキーマ変更が必要 | ✅ 既存DBに変更不要 |
| **柔軟性** | ❌ Salesforce/Asteria共通スキーマ制約 | ✅ 各々に最適化されたスキーマ |
| **移行** | ✅ 今後別OEM追加も統合可能 | ⚠️ OEM毎にDBが増える |
| **バックアップ** | ✅ 1つのバックアップで完了 | ⚠️ 複数のバックアップが必要 |

### Recommendation: **統合DB (sourceカラム方式)**

**理由**:
1. 検索体験が最優先（1箇所で検索完了）
2. case_search.py 修正不要でAsteriaケースも検索可能
3. 今後別OEM（BeaCoun等）追加時も統合可能

### Implementation Strategy

1. **既存DBのバックアップ** → 失敗時にロールバック可能
2. **sourceカラム追加** → 既存レコードは 'salesforce' で埋める
3. **FTSテーブルも拡張** → sourceでのフィルタ検索も可能に

```sql
-- マイグレーションスクリプト
BEGIN TRANSACTION;

-- summaries テーブルに source カラム追加
ALTER TABLE summaries ADD COLUMN source TEXT DEFAULT 'salesforce';

-- 既存レコードを明示的に更新
UPDATE summaries SET source='salesforce' WHERE source IS NULL;

-- FTSテーブルにも source を追加（オプション：フィルタ検索用）
DROP TABLE IF EXISTS summaries_fts_trigram;
CREATE VIRTUAL TABLE summaries_fts_trigram USING fts5(
    case_number,
    summary_text,
    source,  -- 追加
    tokenize='trigram'
);

-- 既存データを再投入
INSERT INTO summaries_fts_trigram (case_number, summary_text, source)
SELECT case_number, summary_text, source FROM summaries;

COMMIT;
```

## 統合DBマイグレーション手順（安全なバックアップ / ロールバック）

本章では、既存の **SQLite + FTS5 要約DB** に対して
Asteria（OEM）由来ケースを統合する際の **安全なマイグレーション手順** を定義する。

### 設計原則

* **原則1：既存データは絶対に壊さない**
* **原則2：失敗しても即座に元に戻せる**
* **原則3：何度でも再実行できる（冪等性）**
* **原則4：Salesforce既存検索品質を劣化させない**

---

### 全体フロー概要

```
[0] 現行DBバックアップ
[1] マイグレーション用DBコピー作成
[2] スキーマ差分適用（ALTER / FTS再構築）
[3] Asteriaケース投入（dry-run → 本番）
[4] 整合性チェック
[5] 切り替え or ロールバック判断
```

---

### 0. 現行DBの完全バックアップ（必須）

**対象ファイル**:
- `case_summaries.db`（SQLite本体）
- WAL/SHM が存在する場合は **必ずセットで**

**手順**:
```bash
cd ~/support/summary-backfill/data/
cp case_summaries.db case_summaries.db.bak_$(date +%Y%m%d_%H%M%S)
cp case_summaries.db-wal case_summaries.db-wal.bak_$(date +%Y%m%d_%H%M%S) 2>/dev/null || true
cp case_summaries.db-shm case_summaries.db-shm.bak_$(date +%Y%m%d_%H%M%S) 2>/dev/null || true
```

**ポイント**:
- **必ずプロセス停止状態で取得**
- 圧縮（zip/tar.gz）して別ディレクトリに退避しても良い
- このバックアップが **最終防衛線**

---

### 1. マイグレーション用DBコピー作成（作業用）

**本番DBは直接触らない。**

```bash
cp case_summaries.db case_summaries_migration.db
```

以降のすべての操作は `case_summaries_migration.db` に対して行う。

---

### 2. スキーママイグレーション

#### 2-1. summaries テーブル変更

```sql
-- sourceカラム追加
ALTER TABLE summaries ADD COLUMN source TEXT DEFAULT 'salesforce';

-- 既存レコードを明示的に更新
UPDATE summaries SET source='salesforce' WHERE source IS NULL;
```

※ SQLite は ALTER が弱いため、
将来大きな変更が出た場合は **CREATE → INSERT SELECT → RENAME** 戦略を採用する。

---

#### 2-2. FTS5 テーブル再構築（安全手順）

**方針**:
- **FTSは壊して作り直すもの**
- 検索品質 > 処理速度

**手順**:
```sql
-- 既存FTSを削除
DROP TABLE IF EXISTS summaries_fts_trigram;

-- 新しいFTSを作成（source列を追加）
CREATE VIRTUAL TABLE summaries_fts_trigram USING fts5(
    case_number,
    summary_text,
    source,
    tokenize='trigram'
);

-- 既存データを再投入
INSERT INTO summaries_fts_trigram (case_number, summary_text, source)
SELECT case_number, summary_text, source FROM summaries;
```

---

### 3. Asteriaケース投入（2段階）

#### 3-1. dry-run（必須）

- DB書き込みなし
- パース件数 / スキップ件数 / 想定INSERT件数を確認

```bash
python -m src.batch_processor \
    --html C:/support/asteria/asteria_202501.htm \
    --db ~/support/summary-backfill/data/case_summaries_migration.db \
    --dry-run
```

**確認ポイント**:
- 想定件数が妥当か
- 既存Salesforceケースが誤検知されていないか
- case_number のプレフィックス（`AST-853689`）が正しく付与されているか

---

#### 3-2. 本番投入

```bash
python -m src.batch_processor \
    --html C:/support/asteria/asteria_202501.htm \
    --db ~/support/summary-backfill/data/case_summaries_migration.db
```

**要件**:
- **INSERT OR IGNORE / 既存検知によるスキップ**
- 再実行しても件数が増えない（冪等）

---

### 4. 整合性チェック（必須）

#### 4-1. 件数チェック

```sql
SELECT source, COUNT(*) FROM summaries GROUP BY source;
SELECT source, COUNT(*) FROM summaries_fts_trigram GROUP BY source;
```

- summaries と FTS の件数が一致していること
- Salesforce 既存件数が変化していないこと

---

#### 4-2. 検索回帰テスト

- Salesforce由来の代表ケース番号で検索
- 以前と同等 or それ以上の検索結果が返ること

```bash
python ~/.claude/skills/_shared/case_search.py "OAuth AND Salesforce"
```

---

### 5. 切り替え or ロールバック判断

#### 5-1. 問題なし → 切り替え

```bash
mv case_summaries.db case_summaries.db.old
mv case_summaries_migration.db case_summaries.db
```

※ `.old` は即削除せず、**数日保持**

---

#### 5-2. 問題あり → 即ロールバック

```bash
rm case_summaries_migration.db
cp case_summaries.db.bak_YYYYMMDD_HHMMSS case_summaries.db
```

- **コード修正 → 再マイグレーション**
- データが壊れてもバックアップで即復旧可能

---

### 運用ルール（重要）

- 本番DBに直接 ALTER / INSERT しない
- マイグレーションは **1バージョン = 1作業DB**
- 失敗は「想定された正常系」

---

### 将来拡張メモ

- OEM追加時も **同一手順で横展開可能**
- source 別に件数・品質を定点観測可能
- FTS再構築は「いつでもやり直せる」前提でOK

---

### FTS同期戦略

**現状**: アプリ側でsummariesとFTSの両方を更新（INSERT時）

**追加の安全策**:
- **整合性チェック**: バッチ完了後、summariesとFTSの件数が一致することを確認
- **再構築手順**: 同期ずれが発生した場合にFTSを再構築するスクリプトを用意
  ```python
  def rebuild_fts():
      """FTSテーブルを再構築（同期ずれ recovery 用）"""
      conn = sqlite3.connect(db_path)
      conn.execute("DELETE FROM summaries_fts_trigram")
      conn.execute("""
          INSERT INTO summaries_fts_trigram (case_number, summary_text, source)
          SELECT case_number, summary_text, source FROM summaries
      """)
      conn.commit()
  ```

## Notes

- **Salesforce版との互換性**: `CaseSummarizer` を継承し、共通コードを再利用
- **プロンプト**: 基本はSalesforce版と共通、製品エリア等の文脈調整のみ
- **DB統合**: `source` カラムでSalesforce/Asteriaを区別（統合DB方式）
- **FTS5検索**: 既存の `case_search.py` は修正不要、自動的にAsteriaケースも検索対象に
- **case_numberプレフィックス**: Asteriaケースは `AST-853689` 形式で衝突回避
- **パスの汎用化**: Pathlibを使用し、Windows/WSL両対応にする
- **HTML→テキスト変換**: `<br>`, `<p>`, `<li>` は改行に変換し、ログブロックは温存
