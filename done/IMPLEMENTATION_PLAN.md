# Fix Asteria Case Summarizer Quality Issue

## Problem Summary

Asteriaケースの要約品質が低い（平均420文字 vs Salesforce 733文字）。根本原因は、HTMLパーサーがDIV要素内の技術コンテンツを抽出できていないため、LLMに210文字の薄いデータしか渡されていない。

### 現在の状態
- `ticket.details`: 2544文字（正しく抽出済み）✅
- `timeline[].content`: 0文字（DIV要素を見落とし）❌
- LLMへの入力: 46文字（"[OPENED]" などのマーカーのみ）❌

## Root Cause

`html_parser.py` の `extract_timeline()` メソッド（238-248行）が、DIV要素をスキップしているため：

```python
# 現在の問題コード
elif hasattr(current, "string"):
    content.append(str(current.string))  # text nodeのみ抽出、DIVは無視
```

HTMLの実際の構造：
```html
<b>OPENED by User</b>
<br/>
<DIV>
  <DIV>詳細な技術情報...</DIV>  ← これが抽出されていない
  <DIV>エラーメッセージ...</DIV>
</DIV>
```

## Solution: Approach A - Fix extract_timeline()

**推奨理由:**
1. アーキテクチャの整合性を維持（壊れたコンポーネントを修正）
2. 時系列の粒度を保持（顧客⇔サポートのやり取りを明確化）
3. スコープが明確（1つの関数のみ修正）
4. Salesforceとの一貫性を保つ

### 実装内容

**File:** `/home/user/asteria-case-summarizer/src/html_parser.py`
**Lines:** 236-248

**変更前:**
```python
content = []
current = bold.parent.next_sibling

while current:
    if current.name == "br":
        content.append("\n")
    elif hasattr(current, "name") and current.name == "b":
        break
    elif hasattr(current, "string"):
        content.append(str(current.string))
    current = current.next_sibling
```

**変更後:**
```python
content = []
current = bold.next_sibling  # bold.parentではなくboldから開始

while current:
    if hasattr(current, "name"):
        if current.name == "br":
            content.append("\n")
        elif current.name == "b":
            break
        elif current.name in ("div", "p", "span", "pre", "code"):
            # DIV要素から再帰的にテキストを抽出
            text = current.get_text()
            if text.strip():
                content.append(text)
    elif current and hasattr(current, "string") and current.string:
        text = str(current.string).strip()
        if text:
            content.append(text)

    current = current.next_sibling
```

**主要な変更点:**
1. `bold.next_sibling`から開始（HTML構造解析に基づく）
2. DIV/P/SPANタグを `.get_text()` で処理
3. 空のコンテンツをフィルタリング

## Testing Strategy

### 検証スクリプト（修正後に実行）

```python
def validate_fix():
    parser = AsteriaHTMLParser("asteria_202501.htm")
    fetcher = AsteriaFetcher("asteria_202501.htm")

    # Test ticket 853723 (known detailed case)
    ticket = parser.get_ticket_by_id("853723")
    timeline = parser.extract_timeline(ticket)
    emails = fetcher.convert_ticket_to_emails(ticket)

    # Metric 1: Timeline content length
    total_timeline = sum(len(e.content) for e in timeline)
    print(f"Timeline content: {total_timeline} chars (target: >1500)")
    assert total_timeline > 1500

    # Metric 2: Email body length
    total_email = sum(len(e.text_body) for e in emails)
    print(f"Email content: {total_email} chars (target: >2000)")
    assert total_email > 2000

    # Metric 3: Content ratio
    ratio = total_timeline / len(ticket.details)
    print(f"Content ratio: {ratio:.2f} (target: >0.7)")
    assert ratio > 0.7

    print("✅ All validation checks passed")
```

### 手動検証ケース

以下のチケットで品質を確認：
- `853689`: MSTeams問題（中程度の複雑さ）
- `853723`: PCASales問題（詳細なログあり）
- `853730`: Snowflake認証問題（長いやり取り）

### 成功基準

| メトリック | 修正前 | 目標 |
|-----------|-------|------|
| Timeline content | ~0 chars | >1500 chars |
| Email total | 46 chars | >2000 chars |
| Summary length | 420 chars | >600 chars |

## Deployment Steps

### Phase 1: 修正とテスト
1. `html_parser.py` の `extract_timeline()` を修正
2. 検証スクリプトを実行
3. 3-5件のサンプルケースで手動確認

### Phase 2: バッチ再処理
```bash
cd ~/asteria-case-summarizer
source .venv/bin/activate
python3 src/batch_processor.py \
  --html asteria_202501.htm \
  --db /home/user/support/summary-backfill/data/case_summaries.db \
  --delete-asteria
```

### Phase 3: 品質確認
```sql
-- DBで要約長を確認
SELECT
    AVG(length(symptoms) + length(our_actions) + length(outcome)) as avg_length
FROM summaries
WHERE case_number LIKE 'AST-%';
-- 期待値: 600-800文字
```

## Edge Cases

### 処理済み
- 複数のDIV要素が連続 → `get_text()` が各DIVを処理
- ネストしたDIV構造 → 再帰的に抽出
- 空のタイムラインエントリー（ASSIGNED） → 既存コードで対応

### 追加の安全策
- `get_text()` が失敗した場合のフォールバック（try-except）
- コンテンツ長が異常に短い場合の警告ログ

## Risk Assessment

**リスク:** HTML構造の変更
**対策:** バリデーションチェックでアラート

**リスク:** BeautifulSoupのエッジケース
**対策:** 包括的なテストとフォールバック

**リスク:** Salesforceパイプラインへの影響
**対策:** コード変更はAsteria専用部分のみ（分離済み）

## Critical Files

- `/home/user/asteria-case-summarizer/src/html_parser.py` - 修正対象
- `/home/user/asteria-case-summarizer/src/asteria_fetcher.py` - 検証のみ
- `/home/user/asteria-case-summarizer/src/batch_processor.py` - 再処理に使用
- `/mnt/c/support/asteria/asteria_202501.htm` - テストデータ

## Expected Outcome

修正後のAsteria要約品質がSalesforceと同等レベルになる：
- 具体的な症状の記述（現在: "詳細不明" → 修正後: エラーメッセージ含む）
- 詳細な対応履歴（現在: 113文字 → 修正後: 300文字）
- エラーコードの抽出（現在: 100%なし → 修正後: 適切に抽出）
