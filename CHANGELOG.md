# Changelog

このプロジェクトの変更履歴を記録します。

## [Unreleased]

### Changed
- **メタデータ形式変更**: `from_manager` + `from_manager_confidence` → `resolution_evidence` 形式に変更 (2026-01-15)
  - 変更理由: `summary-backfill` プロジェクトで改良された形式に統一
  - 影響ファイル: `src/llm_summarizer.py`
  - 既存データ: AST-レコード233件は旧形式のまま（再処理は保留）

#### 旧形式（廃止）
```json
{
  "from_manager": "RESOLVED|UNRESOLVED",
  "from_manager_confidence": 0.0-1.0,
  "resolved": true/false
}
```

#### 新形式
```json
{
  "resolution_evidence": {
    "last_actor": "customer|support|none",
    "customer_signal": "confirmed|thanks|will_try|question|no_reply|none",
    "explicit_close": true/false
  }
}
```

### 変更の背景
- 旧形式は解決/未解決の二値判定のみで、根拠が不明確だった
- 新形式はメールスレッドの終わり方を観察した事実ベースの記録
- `summary-backfill` で先行導入・検証済み

### 既存データの扱い
- **AST-レコード（233件）**: 旧形式のまま保持
- 再処理にはLLMによる元メールスレッドの再解析が必要
- 再処理の実施は別途判断

---

## [1.0.0] - 2026-01-14

### Added
- 初期リリース
- Asteriaチケット（HTMLエクスポート）からの要約生成機能
- `summary-backfill` プロジェクトのDBに直接保存
- Gemini 2.5 Flash-Lite によるLLM要約
