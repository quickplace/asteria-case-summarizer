#!/usr/bin/env python3
"""
asteria_summarizer.py - Asteria case summarizer with LLM integration

Processes bugtrack HTML exports and generates structured summaries using Gemini API.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

# Import from Salesforce summarizer (add to path)
SALESFORCE_ROOT = Path(__file__).parent.parent.parent / "salesforce-case-summarizer"
sys.path.insert(0, str(SALESFORCE_ROOT))

try:
    from summarizer import CaseSummarizer, CaseSummary, EmailMessage as SalesforceEmail
except ImportError:
    # Fallback if Salesforce summarizer not available
    SalesforceEmail = None
    CaseSummarizer = object
    CaseSummary = None

from .html_parser import AsteriaHTMLParser, AsteriaTicket
from .asteria_fetcher import AsteriaFetcher, EmailMessage
from .llm_summarizer import LLMSummarizer


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class AsteriaCaseSummary:
    """Asteria case summary data structure"""
    case_number: str
    summary_text: str
    symptoms: Optional[str] = None
    environment: Optional[str] = None
    error_codes: Optional[str] = None
    customer_ask: Optional[str] = None
    our_actions: Optional[str] = None
    outcome: Optional[str] = None
    next_step: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    email_count: int = 0
    date_range: str = ""
    created_at: Optional[datetime] = None


class AsteriaSummarizer:
    """
    Asteria case summarizer with real LLM integration.

    Uses Gemini API (gemini-2.5-flash-lite) for high-quality structured summaries.
    """

    def __init__(
        self,
        html_path: str,
        config_path: str = "config/settings.yaml",
        use_llm: bool = True
    ):
        """
        Initialize Asteria summarizer.

        Args:
            html_path: Path to bugtrack HTML export file
            config_path: Path to configuration file (currently unused, kept for compatibility)
            use_llm: Whether to use LLM for summarization (default: True)
        """
        self.html_path = Path(html_path)
        self.config_path = config_path
        self.use_llm = use_llm

        # Initialize parsers
        self.parser = AsteriaHTMLParser(html_path)
        self.fetcher = AsteriaFetcher(html_path)

        # Initialize LLM summarizer
        self.llm_summarizer = None
        if use_llm:
            try:
                self.llm_summarizer = LLMSummarizer()
                logger.info("LLM summarizer initialized successfully")
            except Exception as e:
                logger.warning(f"Failed to initialize LLM summarizer: {e}")
                logger.warning("Falling back to simple summarization")
                self.use_llm = False

    def process_ticket(self, ticket_id: str) -> Optional[dict]:
        """
        Process a single Asteria ticket and generate summary.

        Args:
            ticket_id: Ticket ID (e.g., "853689")

        Returns:
            Dictionary with summary data or None if failed
        """
        # 1. Get ticket from HTML
        ticket = self.parser.get_ticket_by_id(ticket_id)

        if not ticket:
            logger.error(f"Ticket {ticket_id} not found")
            return None

        logger.info(f"Processing ticket {ticket_id}: {ticket.title[:60]}...")

        # 2. Convert to EmailMessage format
        emails = self.fetcher.convert_ticket_to_emails(ticket)

        if not emails:
            logger.warning(f"No emails extracted from ticket {ticket_id}")
            return None

        logger.info(f"Extracted {len(emails)} message(s) from timeline")

        # 3. Merge emails into thread
        email_thread = self._merge_emails(emails)
        date_range = f"{emails[0].message_date.date()} - {emails[-1].message_date.date()}"

        # 4. Generate summary
        if self.use_llm and self.llm_summarizer:
            summary = self._generate_summary_with_llm(ticket, emails, email_thread, date_range)
        else:
            summary = self._generate_summary_simple(ticket, emails, email_thread, date_range)

        # Add Asteria-specific metadata
        if summary:
            summary["metadata"] = summary.get("metadata", {})
            summary["metadata"]["source"] = "asteria"
            summary["metadata"]["area"] = ticket.area
            summary["metadata"]["priority"] = ticket.priority
            summary["metadata"]["importance"] = ticket.importance
            summary["metadata"]["ticket_id"] = ticket.ticket_id
            summary["metadata"]["title"] = ticket.title

        return summary

    def _merge_emails(self, emails: List[EmailMessage]) -> str:
        """Merge emails into thread format."""
        parts = []
        for email in emails:
            direction = "【顧客→サポート】" if email.is_incoming else "【サポート→顧客】"
            timestamp = email.message_date.strftime("%Y-%m-%d %H:%M")
            parts.append(f"{direction} {timestamp}")
            parts.append(email.text_body)
            parts.append("")  # blank line separator

        return "\n".join(parts)

    def _generate_summary_with_llm(
        self,
        ticket: AsteriaTicket,
        emails: List[EmailMessage],
        email_thread: str,
        date_range: str,
    ) -> Optional[dict]:
        """Generate summary using LLM."""
        try:
            # Call LLM API
            parsed, raw_text = self.llm_summarizer.generate_summary(
                ticket_id=ticket.ticket_id,
                area=ticket.area,
                priority=ticket.priority,
                importance=ticket.importance,
                email_thread=email_thread,
                email_count=len(emails),
                date_range=date_range,
            )

            # Build summary text from parsed sections
            summary_text = self.llm_summarizer.build_summary_text(parsed)

            return {
                "case_number": ticket.ticket_id,
                "summary_text": summary_text,
                "symptoms": parsed.get("symptoms"),
                "environment": parsed.get("environment"),
                "error_codes": parsed.get("error_codes"),
                "customer_ask": parsed.get("customer_ask"),
                "our_actions": parsed.get("our_actions"),
                "outcome": parsed.get("outcome"),
                "next_step": parsed.get("next_step"),
                "metadata": parsed.get("metadata", {}),
                "email_count": len(emails),
                "date_range": date_range,
                "created_at": datetime.now().isoformat()
            }

        except Exception as e:
            logger.exception(f"Error generating LLM summary for ticket {ticket.ticket_id}: {e}")
            # Fallback to simple summary
            return self._generate_summary_simple(ticket, emails, email_thread, date_range)

    def _generate_summary_simple(
        self,
        ticket: AsteriaTicket,
        emails: List[EmailMessage],
        email_thread: str,
        date_range: str,
    ) -> Optional[dict]:
        """Generate simple summary without LLM (fallback)."""
        # Extract outcome from ticket
        outcome = self._extract_outcome(ticket)

        # Build simple summary
        summary = {
            "case_number": ticket.ticket_id,
            "summary_text": self._build_simple_summary_text(ticket, email_thread),
            "symptoms": f"{ticket.title}\n\n{ticket.details[:500]}...",
            "environment": f"Area: {ticket.area}",
            "error_codes": "",
            "customer_ask": "",
            "our_actions": "",
            "outcome": outcome,
            "next_step": "完了" if ticket.closed else "未解決",
            "metadata": {
                "title": ticket.title,
                "area": ticket.area,
                "opened": ticket.opened.isoformat(),
                "closed": ticket.closed.isoformat() if ticket.closed else None,
                "fallback_mode": True
            },
            "email_count": len(emails),
            "date_range": date_range,
            "created_at": datetime.now().isoformat()
        }

        return summary

    def _build_simple_summary_text(self, ticket: AsteriaTicket, thread_text: str) -> str:
        """Build simple summary text from ticket details."""
        parts = [
            "## 【AI要約】",
            "",
            f"### ケース番号: {ticket.ticket_id}",
            f"### タイトル: {ticket.title}",
            f"### エリア: {ticket.area}",
            "",
            "### 概要",
            f"{ticket.details[:500]}...",
            "",
            f"### 開始日時: {ticket.opened.isoformat()}",
        ]

        if ticket.closed:
            parts.append(f"### 完了日時: {ticket.closed.isoformat()}")

        return "\n".join(parts)

    def _extract_outcome(self, ticket: AsteriaTicket) -> str:
        """Extract outcome from ticket details."""
        # Check if ticket is closed
        if ticket.closed:
            return "RESOLVED"

        # Check for RESOLVED or CLOSED in timeline
        timeline = self.parser.extract_timeline(ticket)
        for entry in timeline:
            if entry.action_type in ("RESOLVED", "CLOSED"):
                return f"FromManager: {entry.action_type}"

        return "OPEN"

    def process_all(self, limit: Optional[int] = None) -> dict:
        """
        Process all tickets from HTML file.

        Args:
            limit: Maximum number of tickets to process

        Returns:
            Processing results with counts
        """
        tickets = self.parser.parse_all_tickets()

        if limit:
            tickets = tickets[:limit]

        results = {
            "total": len(tickets),
            "success": 0,
            "failed": 0,
            "summaries": []
        }

        for ticket in tickets:
            try:
                summary = self.process_ticket(ticket.ticket_id)
                if summary:
                    results["summaries"].append(summary)
                    results["success"] += 1
                else:
                    results["failed"] += 1
            except Exception as e:
                logger.exception(f"Error processing ticket {ticket.ticket_id}: {e}")
                results["failed"] += 1

        return results


# CLI entry point
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Summarize Asteria cases from bugtrack HTML")
    ap.add_argument("html_file", help="Path to bugtrack HTML export file")
    ap.add_argument("--ticket", help="Specific ticket ID to process")
    ap.add_argument("--limit", type=int, help="Maximum number of tickets to process")
    ap.add_argument("--output", help="Output JSON file path")
    ap.add_argument("--no-llm", action="store_true", help="Disable LLM summarization (use simple mode)")

    args = ap.parse_args()

    summarizer = AsteriaSummarizer(
        args.html_file,
        use_llm=not args.no_llm
    )

    if args.ticket:
        # Process single ticket
        summary = summarizer.process_ticket(args.ticket)

        if summary:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(f"Failed to process ticket {args.ticket}")
            sys.exit(1)
    else:
        # Process all tickets
        results = summarizer.process_all(limit=args.limit)

        print(f"Processed {results['success']}/{results['total']} tickets successfully")
        print(f"Failed: {results['failed']}")

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"Results saved to {args.output}")
