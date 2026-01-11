#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Re-process all Asteria cases with LLM and update DB
"""
import sys
import io
import sqlite3
from pathlib import Path

# Set UTF-8 encoding for Windows console
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Add src to path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

# Import modules directly
import html_parser
import asteria_fetcher
import llm_summarizer

class AsteriaBatchProcessor:
    """Batch process Asteria tickets with LLM"""

    def __init__(self, html_path, db_path):
        self.html_path = Path(html_path)
        self.db_path = Path(db_path)
        self.parser = html_parser.AsteriaHTMLParser(html_path)
        self.fetcher = asteria_fetcher.AsteriaFetcher(html_path)
        self.llm_summarizer = llm_summarizer.LLMSummarizer()
        print("[OK] Initialized LLM batch processor")

    def delete_existing_asteria_cases(self):
        """Delete existing Asteria cases from DB"""
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()

            # Count existing Asteria cases
            cur.execute("SELECT COUNT(*) FROM summaries WHERE case_number LIKE 'AST-%'")
            count = cur.fetchone()[0]
            print(f"[INFO] Found {count} existing Asteria cases")

            if count > 0:
                # Delete from summaries (FTS will be auto-updated by triggers)
                cur.execute("DELETE FROM summaries WHERE case_number LIKE 'AST-%'")
                conn.commit()
                print(f"[OK] Deleted {count} existing Asteria cases")

            conn.close()
            return count

        except Exception as e:
            print(f"[ERROR] Failed to delete existing cases: {e}")
            return 0

    def process_ticket(self, ticket_id):
        """Process single ticket with LLM"""
        from datetime import datetime

        ticket = self.parser.get_ticket_by_id(ticket_id)
        if not ticket:
            return None

        emails = self.fetcher.convert_ticket_to_emails(ticket)
        if not emails:
            return None

        email_thread = self._merge_emails(emails)
        date_range = f"{emails[0].message_date.date()} - {emails[-1].message_date.date()}"

        # Generate LLM summary
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

    def _merge_emails(self, emails):
        parts = []
        for email in emails:
            direction = "【顧客→サポート】" if email.is_incoming else "【サポート→顧客】"
            timestamp = email.message_date.strftime("%Y-%m-%d %H:%M")
            parts.append(f"{direction} {timestamp}")
            parts.append(email.text_body)
            parts.append("")
        return "\n".join(parts)

    def save_to_db(self, summary):
        """Save summary to DB with 7-section structure"""
        import json
        from datetime import datetime

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        case_number = f"AST-{summary['case_number']}"

        # Prepare metadata
        metadata = summary.get("metadata", {})
        metadata["source"] = "asteria"

        # Insert with 7-section structure
        cur.execute("""
            INSERT OR REPLACE INTO summaries
            (case_number, salesforce_case_id, symptoms, environment, error_codes,
             customer_ask, our_actions, outcome, next_step, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            case_number,
            None,
            summary.get("symptoms"),
            summary.get("environment"),
            summary.get("error_codes"),
            summary.get("customer_ask"),
            summary.get("our_actions"),
            summary.get("outcome"),
            summary.get("next_step"),
            json.dumps(metadata, ensure_ascii=False),
            datetime.now().isoformat()
        ))

        conn.commit()
        conn.close()
        print(f"  [OK] Saved {case_number}")

    def process_all(self, limit=None):
        """Process all tickets"""
        tickets = self.parser.parse_all_tickets()

        if limit:
            tickets = tickets[:limit]

        results = {
            "total": len(tickets),
            "success": 0,
            "failed": 0,
        }

        print(f"\n[INFO] Processing {len(tickets)} tickets...")
        print("=" * 60)

        for i, ticket in enumerate(tickets, 1):
            try:
                print(f"\n[{i}/{len(tickets)}] Processing ticket {ticket.ticket_id}...")
                summary = self.process_ticket(ticket.ticket_id)

                if summary:
                    self.save_to_db(summary)
                    results["success"] += 1
                else:
                    print(f"  [WARN] No summary generated")
                    results["failed"] += 1

            except Exception as e:
                print(f"  [ERROR] {e}")
                results["failed"] += 1

        return results


def main():
    html_path = r"C:\support\asteria\asteria_202501.htm"
    # Use WSL DB path
    db_path = r"\\wsl$\Ubuntu\home\user\support\summary-backfill\data\case_summaries.db"

    print("=" * 60)
    print("Asteria Case Re-processing with LLM")
    print("=" * 60)

    processor = AsteriaBatchProcessor(html_path, db_path)

    # Delete existing Asteria cases
    print("\n[STEP 1] Deleting existing Asteria cases...")
    processor.delete_existing_asteria_cases()

    # Process all tickets
    print("\n[STEP 2] Processing tickets with LLM...")
    results = processor.process_all()

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Total: {results['total']}")
    print(f"Success: {results['success']}")
    print(f"Failed: {results['failed']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
