#!/usr/bin/env python3
"""
batch_processor.py - Batch process Asteria cases and save to SQLite DB

Processes all tickets from bugtrack HTML export, generates summaries,
and saves them to the unified SQLite case summaries database.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Import from Salesforce summarizer (add to path)
SALESFORCE_ROOT = Path(__file__).parent.parent.parent / "salesforce-case-summarizer"
sys.path.insert(0, str(SALESFORCE_ROOT))

from .html_parser import AsteriaHTMLParser, AsteriaTicket
from .asteria_summarizer import AsteriaSummarizer


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class AsteriaBatchProcessor:
    """Batch process Asteria tickets and save to SQLite DB."""

    def __init__(
        self,
        html_path: str,
        db_path: str,
        config_path: str = "config/settings.yaml"
    ):
        """
        Initialize batch processor.

        Args:
            html_path: Path to bugtrack HTML export file
            db_path: Path to SQLite case summaries database
            config_path: Path to configuration file
        """
        self.html_path = Path(html_path)
        self.db_path = Path(db_path)
        self.config_path = config_path

        # Initialize parser and summarizer
        self.parser = AsteriaHTMLParser(html_path)
        self.summarizer = AsteriaSummarizer(html_path, config_path)

        # Validate DB exists
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")

    def process_all(
        self,
        limit: Optional[int] = None,
        dry_run: bool = False
    ) -> Dict:
        """
        Process all tickets and save to DB.

        Args:
            limit: Maximum number of tickets to process
            dry_run: If True, skip DB writes

        Returns:
            Processing results with counts
        """
        tickets = self.parser.parse_all_tickets()

        if limit:
            tickets = tickets[:limit]

        logger.info(f"Processing {len(tickets)} tickets (dry_run={dry_run})")

        results = {
            "total": len(tickets),
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "summaries": []
        }

        for ticket in tickets:
            try:
                # Check if already exists in DB
                if self._exists_in_db(ticket.ticket_id):
                    logger.info(f"case=AST-{ticket.ticket_id} source=asteria status=skipped(reason=already_exists)")
                    results["skipped"] += 1
                    continue

                # Generate summary
                summary = self.summarizer.process_ticket(ticket.ticket_id)

                if summary:
                    if not dry_run:
                        self._save_to_db(summary, source='asteria')
                        logger.info(f"case=AST-{ticket.ticket_id} source=asteria status=success")

                    results["summaries"].append(summary)
                    results["success"] += 1
                else:
                    logger.warning(f"case=AST-{ticket.ticket_id} source=asteria status=failed(reason=no_summary)")
                    results["failed"] += 1

            except Exception as e:
                logger.exception(f"case=AST-{ticket.ticket_id} source=asteria status=failed(error={e})")
                results["failed"] += 1

        # Log summary
        logger.info(
            f"Batch complete: total={results['total']}, "
            f"success={results['success']}, "
            f"failed={results['failed']}, "
            f"skipped={results['skipped']}"
        )

        # Verify DB integrity if not dry run
        if not dry_run:
            self._verify_db_integrity()

        return results

    def _exists_in_db(self, ticket_id: str) -> bool:
        """Check if ticket already exists in DB."""
        case_number = f"AST-{ticket_id}"

        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM summaries WHERE case_number = ? LIMIT 1",
                (case_number,)
            )
            exists = cur.fetchone() is not None
            conn.close()
            return exists
        except Exception as e:
            logger.error(f"Error checking existence: {e}")
            return False

    def _save_to_db(self, summary: dict, source: str):
        """Save summary to SQLite DB with 7-section structure."""
        import json

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        # Add prefix to case_number for collision avoidance
        case_number = f"AST-{summary['case_number']}"

        # Prepare metadata
        metadata = summary.get("metadata", {})
        metadata["source"] = source

        # Check if summary has structured sections (LLM mode)
        if summary.get("symptoms") and summary.get("environment"):
            # LLM-generated 7-section summary
            cur.execute("""
                INSERT OR REPLACE INTO summaries
                (case_number, salesforce_case_id, symptoms, environment, error_codes,
                 customer_ask, our_actions, outcome, next_step, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                case_number,
                None,  # salesforce_case_id is NULL for Asteria cases
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
        else:
            # Fallback mode: store full summary in symptoms field
            summary_text = summary.get("summary_text", "")
            cur.execute("""
                INSERT OR REPLACE INTO summaries
                (case_number, salesforce_case_id, symptoms, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                case_number,
                None,
                summary_text,
                json.dumps(metadata, ensure_ascii=False),
                datetime.now().isoformat()
            ))

        # FTS sync is handled by triggers
        conn.commit()
        conn.close()

    def _parse_summary_sections(self, summary_text: str) -> dict:
        """Parse summary text into 7 sections."""
        sections = {
            "symptoms": "",
            "environment": "",
            "error_codes": "",
            "customer_ask": "",
            "our_actions": "",
            "outcome": "",
            "next_step": ""
        }

        # Pattern: ## Section Name（Japanese）\nContent\n\n## Next Section
        # Split by ## to find sections
        lines = summary_text.split('\n')
        current_section = None
        current_content = []

        section_map = {
            "Symptoms": "symptoms",
            "Environment": "environment",
            "Error codes": "error_codes",
            "Customer ask": "customer_ask",
            "Our actions": "our_actions",
            "Outcome": "outcome",
            "Next step": "next_step"
        }

        for line in lines:
            # Check if line is a section header
            if line.startswith("## "):
                # Save previous section
                if current_section:
                    sections[current_section] = '\n'.join(current_content).strip()

                # Parse new section
                for en_name, jp_name in [
                    ("Symptoms", "現象"),
                    ("Environment", "環境"),
                    ("Error codes", "エラーコード"),
                    ("Customer ask", "顧客要望"),
                    ("Our actions", "対応内容"),
                    ("Outcome", "結果"),
                    ("Next step", "次のステップ")
                ]:
                    if en_name in line or jp_name in line:
                        current_section = section_map.get(en_name)
                        current_content = []
                        break
            elif current_section:
                current_content.append(line)

        # Save last section
        if current_section:
            sections[current_section] = '\n'.join(current_content).strip()

        return sections

    def _verify_db_integrity(self):
        """Verify summaries and FTS tables have matching counts."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()

            # Count all summaries
            cur.execute("SELECT COUNT(*) FROM summaries")
            summary_count = cur.fetchone()[0]

            # Count FTS entries
            try:
                cur.execute("SELECT COUNT(*) FROM summaries_fts")
                fts_count = cur.fetchone()[0]

                if summary_count != fts_count:
                    logger.warning(
                        f"Integrity check: summaries={summary_count} fts={fts_count} - mismatch detected"
                    )
                else:
                    logger.info(f"Integrity check passed: {summary_count} summaries, {fts_count} FTS entries")

            except sqlite3.OperationalError:
                logger.warning("FTS table not found - may need migration")

            conn.close()

        except Exception as e:
            logger.error(f"Error verifying DB integrity: {e}")


# CLI entry point
if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Batch process Asteria cases and save to SQLite DB"
    )
    ap.add_argument(
        "--html",
        required=True,
        help="Path to bugtrack HTML export file"
    )
    ap.add_argument(
        "--db",
        required=True,
        help="Path to SQLite case summaries database"
    )
    ap.add_argument(
        "--limit",
        type=int,
        help="Maximum number of tickets to process"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and summarize without writing to DB"
    )
    ap.add_argument(
        "--delete-asteria",
        action="store_true",
        help="Delete existing Asteria cases before processing"
    )

    args = ap.parse_args()

    processor = AsteriaBatchProcessor(
        html_path=args.html,
        db_path=args.db,
    )

    # Delete existing Asteria cases if requested
    if args.delete_asteria:
        logger.info("Deleting existing Asteria cases...")
        try:
            conn = sqlite3.connect(processor.db_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM summaries WHERE case_number LIKE 'AST-%'")
            count = cur.fetchone()[0]
            logger.info(f"Found {count} existing Asteria cases")
            if count > 0:
                cur.execute("DELETE FROM summaries WHERE case_number LIKE 'AST-%'")
                conn.commit()
                logger.info(f"Deleted {count} existing Asteria cases")
            conn.close()
        except Exception as e:
            logger.error(f"Failed to delete existing cases: {e}")

    results = processor.process_all(
        limit=args.limit,
        dry_run=args.dry_run
    )

    print("\n=== Batch Processing Results ===")
    print(f"Total tickets: {results['total']}")
    print(f"Success: {results['success']}")
    print(f"Failed: {results['failed']}")
    print(f"Skipped: {results['skipped']}")

    if args.dry_run:
        print("\n[DRY RUN] No database writes performed")
