#!/usr/bin/env python3
"""
asteria_summarizer.py - Asteria case summarizer inheriting from Salesforce version

Extends CaseSummarizer to work with Asteria bugtrack HTML exports.
Converts Asteria tickets to EmailMessage format and uses existing summarization logic.
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
from typing import List, Optional

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


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class AsteriaSummarizer:
    """
    Asteria case summarizer that processes bugtrack HTML exports.

    Can work standalone or extend Salesforce CaseSummarizer if available.
    """

    def __init__(self, html_path: str, config_path: str = "config/settings.yaml"):
        """
        Initialize Asteria summarizer.

        Args:
            html_path: Path to bugtrack HTML export file
            config_path: Path to configuration file
        """
        self.html_path = Path(html_path)
        self.config_path = config_path

        # Initialize parsers
        self.parser = AsteriaHTMLParser(html_path)
        self.fetcher = AsteriaFetcher(html_path)

        # Try to initialize Salesforce summarizer for reusing logic
        self.sf_summarizer = None
        if CaseSummarizer is not None:
            try:
                self.sf_summarizer = CaseSummarizer(config_path)
                logger.info("Salesforce CaseSummarizer loaded - reusing summarization logic")
            except Exception as e:
                logger.warning(f"Could not initialize Salesforce summarizer: {e}")

        # Load config directly if Salesforce summarizer not available
        self.config = self._load_config() if self.sf_summarizer is None else {}

    def _load_config(self) -> dict:
        """Load configuration from YAML file."""
        import yaml

        config_full_path = Path(self.config_path)
        if config_full_path.exists():
            with open(config_full_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {}

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

        # 3. Generate summary (using Salesforce summarizer if available)
        if self.sf_summarizer:
            summary = self._generate_summary_with_sf(ticket, emails)
        else:
            summary = self._generate_summary_standalone(ticket, emails)

        # Add Asteria-specific metadata
        if summary:
            summary["metadata"] = summary.get("metadata", {})
            summary["metadata"]["source"] = "asteria"
            summary["metadata"]["area"] = ticket.area
            summary["metadata"]["priority"] = ticket.priority
            summary["metadata"]["importance"] = ticket.importance
            summary["metadata"]["ticket_id"] = ticket.ticket_id

        return summary

    def _generate_summary_with_sf(
        self,
        ticket: AsteriaTicket,
        emails: List[EmailMessage]
    ) -> Optional[dict]:
        """Generate summary using Salesforce CaseSummarizer logic."""
        try:
            # Convert our EmailMessage to Salesforce EmailMessage format
            sf_emails = [
                SalesforceEmail(
                    message_id=e.message_id,
                    subject=e.subject,
                    from_address=e.from_address,
                    to_address=e.to_address,
                    text_body=e.text_body,
                    message_date=e.message_date,
                    is_incoming=e.is_incoming
                )
                for e in emails
            ]

            # Merge emails (using Salesforce logic if available)
            if hasattr(self.sf_summarizer, 'merge_emails'):
                email_thread = self.sf_summarizer.merge_emails(sf_emails)
                thread_text = email_thread
            else:
                thread_text = self._merge_emails_simple(sf_emails)

            # Generate summary (using Salesforce logic if available)
            if hasattr(self.sf_summarizer, 'generate_summary'):
                case_summary = self.sf_summarizer.generate_summary(ticket_id, thread_text)
                return self._case_summary_to_dict(case_summary)
            else:
                return self._generate_summary_simple(ticket, thread_text)

        except Exception as e:
            logger.exception(f"Error generating summary with Salesforce summarizer: {e}")
            return None

    def _generate_summary_standalone(
        self,
        ticket: AsteriaTicket,
        emails: List[EmailMessage]
    ) -> Optional[dict]:
        """Generate summary without Salesforce CaseSummarizer (fallback)."""
        thread_text = self._merge_emails_simple(emails)

        # Build simple summary from ticket details
        summary = {
            "case_number": ticket.ticket_id,
            "summary_text": self._build_simple_summary(ticket, thread_text),
            "outcome": self._extract_outcome(ticket),
            "metadata": {
                "title": ticket.title,
                "area": ticket.area,
                "opened": ticket.opened.isoformat(),
                "closed": ticket.closed.isoformat() if ticket.closed else None,
                "email_count": len(emails)
            },
            "email_count": len(emails),
            "date_range": f"{emails[0].message_date.date()} - {emails[-1].message_date.date()}",
            "created_at": datetime.now().isoformat()
        }

        return summary

    def _merge_emails_simple(self, emails: List) -> str:
        """Simple email merge when Salesforce summarizer not available."""
        parts = []
        for email in emails:
            direction = "【顧客→サポート】" if email.is_incoming else "【サポート→顧客】"
            timestamp = email.message_date.strftime("%Y-%m-%d %H:%M")
            parts.append(f"{direction} {timestamp} {email.from_address}")
            parts.append(email.text_body)
            parts.append("")  # blank line separator

        return "\n".join(parts)

    def _build_simple_summary(self, ticket: AsteriaTicket, thread_text: str) -> str:
        """Build simple summary from ticket details."""
        parts = [
            f"## 【AI要約】",
            "",
            f"### ケース番号: {ticket.ticket_id}",
            f"### タイトル: {ticket.title}",
            f"### エリア: {ticket.area}",
            f"### 優先度: {ticket.priority}",
            f"### 重要度: {ticket.importance}",
            "",
            "### 概要",
            f"{ticket.details[:500]}...",
            "",
            f"### 開始日時: {ticket.opened.isoformat()}",
        ]

        if ticket.closed:
            parts.append(f"### 完了日時: {ticket.closed.isoformat()}")

        parts.extend([
            "",
            "---",
            f"Meta: emails={len(ticket.ticket_id)}, area={ticket.area}"
        ])

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

    def _case_summary_to_dict(self, case_summary) -> dict:
        """Convert Salesforce CaseSummary to dict."""
        return {
            "case_number": case_summary.case_number,
            "summary_text": case_summary.summary_text,
            "outcome": getattr(case_summary, 'outcome', 'UNKNOWN'),
            "metadata": case_summary.metadata,
            "email_count": case_summary.email_count,
            "date_range": case_summary.date_range,
            "created_at": case_summary.created_at.isoformat()
        }

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

    args = ap.parse_args()

    summarizer = AsteriaSummarizer(args.html_file)

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
