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
        """Save summary to SQLite DB."""
        import json

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        # Add prefix to case_number for collision avoidance
        case_number = f"AST-{summary['case_number']}"

        # Prepare metadata
        metadata = summary.get("metadata", {})
        metadata["source"] = source

        cur.execute("""
            INSERT OR REPLACE INTO summaries
            (case_number, summary_text, outcome, metadata, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            case_number,
            summary["summary_text"],
            summary.get("outcome", "UNKNOWN"),
            json.dumps(metadata, ensure_ascii=False),
            source,
            datetime.now()
        ))

        # FTSテーブルにも登録（source列を含む3列）
        try:
            cur.execute("""
                INSERT OR REPLACE INTO summaries_fts_trigram
                (case_number, summary_text, source)
                VALUES (?, ?, ?)
            """, (case_number, summary["summary_text"], source))
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS insert failed (may need migration): {e}")

        conn.commit()
        conn.close()

    def _verify_db_integrity(self):
        """Verify summaries and FTS tables have matching counts."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()

            # Count by source
            cur.execute("SELECT source, COUNT(*) FROM summaries GROUP BY source")
            summary_counts = dict(cur.fetchall())

            # Try FTS count
            try:
                cur.execute("SELECT source, COUNT(*) FROM summaries_fts_trigram GROUP BY source")
                fts_counts = dict(cur.fetchall())

                # Check for mismatches
                for source in summary_counts:
                    if summary_counts[source] != fts_counts.get(source, 0):
                        logger.warning(
                            f"Integrity check: source={source} "
                            f"summaries={summary_counts[source]} "
                            f"fts={fts_counts.get(source, 0)} - mismatch detected"
                        )
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

    args = ap.parse_args()

    processor = AsteriaBatchProcessor(
        html_path=args.html,
        db_path=args.db,
    )

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
