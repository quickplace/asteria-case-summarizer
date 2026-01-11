#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script for LLM summarization
"""
import sys
import os
import io
from pathlib import Path

# Set UTF-8 encoding for Windows console
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Add src to path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

# Import modules directly to avoid relative import issues
import html_parser
import asteria_fetcher
import llm_summarizer

class AsteriaSummarizer:
    """Simplified version for testing"""
    def __init__(self, html_path, use_llm=True):
        self.html_path = Path(html_path)
        self.parser = html_parser.AsteriaHTMLParser(html_path)
        self.fetcher = asteria_fetcher.AsteriaFetcher(html_path)
        self.use_llm = use_llm

        if use_llm:
            try:
                self.llm_summarizer = llm_summarizer.LLMSummarizer()
                print("[OK] LLM summarizer initialized")
            except Exception as e:
                print(f"[WARN] Failed to initialize LLM: {e}")
                self.use_llm = False

    def process_ticket(self, ticket_id):
        ticket = self.parser.get_ticket_by_id(ticket_id)
        if not ticket:
            return None

        emails = self.fetcher.convert_ticket_to_emails(ticket)
        if not emails:
            return None

        email_thread = self._merge_emails(emails)
        date_range = f"{emails[0].message_date.date()} - {emails[-1].message_date.date()}"

        if self.use_llm and self.llm_summarizer:
            return self._generate_summary_with_llm(ticket, emails, email_thread, date_range)
        else:
            return self._generate_summary_simple(ticket, emails, email_thread, date_range)

    def _merge_emails(self, emails):
        parts = []
        for email in emails:
            direction = "【顧客→サポート】" if email.is_incoming else "【サポート→顧客】"
            timestamp = email.message_date.strftime("%Y-%m-%d %H:%M")
            parts.append(f"{direction} {timestamp}")
            parts.append(email.text_body)
            parts.append("")
        return "\n".join(parts)

    def _generate_summary_with_llm(self, ticket, emails, email_thread, date_range):
        from datetime import datetime
        parsed, raw_text = self.llm_summarizer.generate_summary(
            ticket_id=ticket.ticket_id,
            area=ticket.area,
            priority=ticket.priority,
            importance=ticket.importance,
            email_thread=email_thread,
            email_count=len(emails),
            date_range=date_range,
        )
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

    def _generate_summary_simple(self, ticket, emails, email_thread, date_range):
        from datetime import datetime
        return {
            "case_number": ticket.ticket_id,
            "summary_text": f"Simple summary for {ticket.ticket_id}",
            "symptoms": ticket.title,
            "environment": f"Area: {ticket.area}",
            "email_count": len(emails),
            "date_range": date_range,
            "metadata": {"fallback_mode": True}
        }

def main():
    html_path = r"C:\support\asteria\asteria_202501.htm"
    ticket_id = "853689"

    print(f"Testing LLM summarization for ticket {ticket_id}...")
    print("=" * 60)

    # Initialize summarizer with LLM enabled
    summarizer = AsteriaSummarizer(html_path, use_llm=True)

    # Process single ticket
    summary = summarizer.process_ticket(ticket_id)

    if summary:
        print("\n[SUCCESS] LLM summary generated.")
        print("=" * 60)
        print(f"Case Number: {summary['case_number']}")
        print(f"Email Count: {summary['email_count']}")
        print(f"Date Range: {summary['date_range']}")
        print(f"LLM Mode: {'No' if summary.get('metadata', {}).get('fallback_mode') else 'Yes'}")
        print("=" * 60)

        # Show sections
        print("\n[SECTIONS]")
        for section in ["symptoms", "environment", "error_codes", "customer_ask", "our_actions", "outcome", "next_step"]:
            if summary.get(section):
                print(f"\n--- {section.upper()} ---")
                text = summary[section]
                print(text[:200] + "..." if len(text) > 200 else text)

        # Show metadata
        if summary.get("metadata"):
            print(f"\n[METADATA]")
            for key, value in summary["metadata"].items():
                if key != "source":
                    print(f"  {key}: {value}")

    else:
        print("\n[FAILED] Could not generate summary")
        sys.exit(1)

if __name__ == "__main__":
    main()
