#!/usr/bin/env python3
"""
llm_summarizer.py - Gemini APIを使用したLLM要約生成

summary-backfillプロジェクトの本番実装をベースに、
Asteriaケース専用のLLM要約生成を行う。
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from typing import Any, Dict, Optional, Tuple

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Gemini設定
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = "gemini-2.5-flash-lite"  # Flash-Lite（コスト最適化）
DEFAULT_INTERVAL = 4.0  # 15 RPM = 4秒間隔

# リトライ設定
MAX_RETRIES = 5
MAX_BACKOFF_TIME = 300  # 最大バックオフ時間（秒）
BASE_BACKOFF_TIME = 60  # 429エラー時の基本待機時間（秒）


def get_asteria_prompt_template() -> str:
    """Asteriaケース用プロンプトテンプレート"""
    return """あなたはテクニカルサポートケースの要約を作成するアシスタントです。

以下のAsteria（OEM先）サポートケースを分析し、構造化された要約を作成してください。

## 入力
チケット番号: {ticket_id}
製品エリア: {area}
優先度: {priority}
重要度: {importance}
メール数: {email_count}

メールスレッド:
{email_thread}

## 出力形式

以下のフォーマットで要約を作成してください。各セクションは簡潔に、重要な情報のみを含めてください。

## Symptoms（現象）
[顧客が報告した問題・症状を1-3文で記述]

## Environment（環境）
- エリア: {area}
- 製品: [CData製品名 - 「Drivers/XXX」の形式]
- バージョン: [バージョン番号、不明な場合は「不明」]
- 接続先: [データソース名]
- OS/環境: [該当する場合のみ]

## Error codes
[エラーコード・エラーメッセージをリスト形式で。なければ「なし」]

## Customer ask（顧客要望）
[顧客が最終的に求めていることを1-2文で]

## Our actions（対応内容）
[CData Japan Supportが行った主要な対応をリスト形式で、時系列順]

## Outcome（結果）
[解決/未解決/進行中、および結果の概要]
**FromManager: [RESOLVED|UNRESOLVED]**

## Next step
[次のアクション。完了済みの場合は「完了」]

---
Meta: emails={email_count}, range={date_range}
Keywords: {keywords}
Summary generated: {generated_at} ({model_name})

## 要約ルール

1. **事実のみ記載**: 推測や仮定は含めない
2. **技術用語保持**: エラーコード、製品名、設定名はそのまま残す
3. **簡潔性**: 各セクション最大3文程度
4. **検索性**: 重要なキーワードは必ず含める
5. **機密除外**: 顧客名、会社名、個人情報は含めない
6. **Asteria特有**: これはOEM（Asteria）経由のお客様ケースであることに注意

## FromManager判定基準

**RESOLVED（解決）** - 以下のいずれかに該当:
- 顧客（Asteria担当者）が問題解決を確認・報告した
- 顧客がクローズを了承した
- 顧客が「ありがとう」「解決しました」等の感謝・完了メッセージを送信した
- サポートの提案により問題が解消された
- RESOLVEDまたはCLOSEDステータスで完了

**UNRESOLVED（未解決）** - 以下のいずれかに該当:
- 製品の機能不足・制限により実現不可だった
- バグ修正待ちでワークアラウンドなし
- サポート返信後、顧客から返信がないままクローズ
- 顧客が問題未解決のままクローズを希望した

## メタデータ抽出

要約とは別に、以下のJSONを出力してください。

```json
{{
  "category": "[Bug|Configuration Issue|How-to|Feature Request|Performance|Other]",
  "product": "[製品名]",
  "area": "{area}",
  "data_source": "[接続先データソース]",
  "error_codes": ["エラーコードのリスト"],
  "resolution_type": "[設定変更|バグ修正待ち|ワークアラウンド|仕様説明|未解決]",
  "resolved": true/false,
  "from_manager": "[RESOLVED|UNRESOLVED]",
  "from_manager_confidence": 0.0-1.0,
  "temperature": "[calm|normal|frustrated|urgent]",
  "faq_candidate": true/false,
  "keywords": ["検索用キーワードリスト"]
}}
```
"""


class LLMSummarizer:
    """Gemini APIを使用したLLM要約生成クラス"""

    def __init__(self, api_key: Optional[str] = None, model_name: str = MODEL_NAME):
        """
        初期化

        Args:
            api_key: Gemini APIキー（省略時は環境変数から取得）
            model_name: モデル名
        """
        self.api_key = api_key or GEMINI_API_KEY
        self.model_name = model_name
        self.prompt_template = get_asteria_prompt_template()
        self.last_call_time = 0.0

        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set in environment variables")

        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(model_name)

        logger.info(f"LLMSummarizer initialized with model: {model_name}")

    def _wait_for_rate_limit(self, interval: float = DEFAULT_INTERVAL) -> None:
        """レート制限を守るための待機"""
        elapsed = time.time() - self.last_call_time
        if elapsed < interval:
            wait_time = interval - elapsed
            logger.debug(f"Rate limit: waiting {wait_time:.1f} seconds")
            time.sleep(wait_time)

    def _handle_rate_limit_with_backoff(self, attempt: int) -> float:
        """
        429エラー時の指数バックオフ待機時間を計算

        Args:
            attempt: リトライ回数（0始まり）

        Returns:
            待機時間（秒）
        """
        # 指数バックオフ: base * 2^attempt + jitter
        base_wait = BASE_BACKOFF_TIME * (2 ** attempt)
        jitter = random.uniform(0.8, 1.2)
        wait_time = min(base_wait * jitter, MAX_BACKOFF_TIME)
        return wait_time

    def generate_summary(
        self,
        ticket_id: str,
        area: str,
        priority: str,
        importance: str,
        email_thread: str,
        email_count: int,
        date_range: str,
        keywords: str = "",
        interval: float = DEFAULT_INTERVAL,
    ) -> Tuple[Dict[str, Any], str]:
        """
        LLM要約を生成（リトライ機能付き）

        Args:
            ticket_id: チケット番号
            area: 製品エリア
            priority: 優先度
            importance: 重要度
            email_thread: メールスレッド（EmailMessage形式に変換済み）
            email_count: メール数
            date_range: 日付範囲
            keywords: キーワード（オプション）
            interval: API呼び出し間隔（秒）

        Returns:
            (parsed_summary, raw_response) のタプル
        """
        from datetime import datetime

        self._wait_for_rate_limit(interval)

        prompt = self.prompt_template.format(
            ticket_id=ticket_id,
            area=area,
            priority=priority,
            importance=importance,
            email_thread=email_thread,
            email_count=email_count,
            date_range=date_range,
            keywords=keywords,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            model_name=self.model_name,
        )

        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"Calling Gemini API for ticket {ticket_id} (attempt {attempt + 1}/{MAX_RETRIES})")
                response = self.model.generate_content(prompt)
                self.last_call_time = time.time()

                raw_text = response.text
                parsed = self._parse_response(ticket_id, raw_text)

                logger.info(f"Successfully generated summary for ticket {ticket_id}")
                return parsed, raw_text

            except google_exceptions.ResourceExhausted as e:
                # 429 Rate Limit エラー
                wait_time = self._handle_rate_limit_with_backoff(attempt)
                logger.warning(
                    f"Rate limit hit (429) for ticket {ticket_id}. "
                    f"Attempt {attempt + 1}/{MAX_RETRIES}. "
                    f"Waiting {wait_time:.1f} seconds..."
                )
                time.sleep(wait_time)
                last_error = e

            except google_exceptions.DeadlineExceeded as e:
                # タイムアウト
                wait_time = self._handle_rate_limit_with_backoff(attempt)
                logger.warning(
                    f"Timeout for ticket {ticket_id}. "
                    f"Attempt {attempt + 1}/{MAX_RETRIES}. "
                    f"Waiting {wait_time:.1f} seconds..."
                )
                time.sleep(wait_time)
                last_error = e

            except google_exceptions.ServiceUnavailable as e:
                # サービス一時停止
                wait_time = self._handle_rate_limit_with_backoff(attempt)
                logger.warning(
                    f"Service unavailable for ticket {ticket_id}. "
                    f"Attempt {attempt + 1}/{MAX_RETRIES}. "
                    f"Waiting {wait_time:.1f} seconds..."
                )
                time.sleep(wait_time)
                last_error = e

            except Exception as e:
                # その他のエラーは即座に失敗
                raise RuntimeError(f"Gemini API error: {str(e)}")

        # 全リトライ失敗
        raise RuntimeError(
            f"Gemini API error after {MAX_RETRIES} retries: {str(last_error)}"
        )

    def _parse_response(self, ticket_id: str, raw_text: str) -> Dict[str, Any]:
        """
        Geminiレスポンスをパース

        7セクション要約とメタデータJSONを抽出

        Args:
            ticket_id: チケット番号
            raw_text: Geminiからの生レスポンス

        Returns:
            パース済みサマリー（辞書形式）
        """
        result = {
            "case_number": ticket_id,
            "symptoms": None,
            "environment": None,
            "error_codes": None,
            "customer_ask": None,
            "our_actions": None,
            "outcome": None,
            "next_step": None,
            "metadata": {},
        }

        # セクション抽出（正規表現パターン）
        sections = {
            "symptoms": r"##\s*Symptoms[（(]?現象[）)]?\s*\n(.*?)(?=\n##|\n---|\n```|$)",
            "environment": r"##\s*Environment[（(]?環境[）)]?\s*\n(.*?)(?=\n##|\n---|\n```|$)",
            "error_codes": r"##\s*Error codes?\s*\n(.*?)(?=\n##|\n---|\n```|$)",
            "customer_ask": r"##\s*Customer ask[（(]?顧客要望[）)]?\s*\n(.*?)(?=\n##|\n---|\n```|$)",
            "our_actions": r"##\s*Our actions[（(]?対応内容[）)]?\s*\n(.*?)(?=\n##|\n---|\n```|$)",
            "outcome": r"##\s*Outcome[（(]?結果[）)]?\s*\n(.*?)(?=\n##|\n---|\n```|$)",
            "next_step": r"##\s*Next step\s*\n(.*?)(?=\n##|\n---|\n```|$)",
        }

        # 各セクションを抽出
        for key, pattern in sections.items():
            match = re.search(pattern, raw_text, re.DOTALL | re.IGNORECASE)
            if match:
                result[key] = match.group(1).strip()

        # メタデータJSON抽出
        json_pattern = r"```json\s*\n(.*?)\n```"
        json_match = re.search(json_pattern, raw_text, re.DOTALL)
        if json_match:
            try:
                metadata = json.loads(json_match.group(1))
                result["metadata"] = metadata
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse metadata JSON: {e}")

        return result

    def build_summary_text(self, parsed: Dict[str, Any]) -> str:
        """
        パース済みサマリーからテキスト形式を構築

        Args:
            parsed: パース済みサマリー

        Returns:
            要約テキスト
        """
        parts = []

        # ヘッダー
        parts.append("## 【AI要約】")
        parts.append("")
        parts.append(f"### ケース番号: {parsed['case_number']}")
        parts.append("")

        # 各セクション
        for section in ["symptoms", "environment", "error_codes", "customer_ask", "our_actions", "outcome", "next_step"]:
            if parsed.get(section):
                section_name = section.replace("_", " ").title()
                parts.append(f"## {section_name}")
                parts.append(parsed[section])
                parts.append("")

        return "\n".join(parts)
