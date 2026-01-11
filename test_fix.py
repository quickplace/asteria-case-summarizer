#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""検証スクリプト：html_parser.py の修正を検証"""
import sys
import io
from pathlib import Path

# Set UTF-8 encoding for Windows console
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Add src to path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

from html_parser import AsteriaHTMLParser
from asteria_fetcher import AsteriaFetcher

def validate_fix():
    html_path = r"C:\support\asteria\asteria_202501.htm"
    ticket_id = "853723"  # PCASales問題（詳細なログあり）

    print(f"=== Testing ticket {ticket_id} ===")
    parser = AsteriaHTMLParser(html_path)
    fetcher = AsteriaFetcher(html_path)

    ticket = parser.get_ticket_by_id(ticket_id)
    if not ticket:
        print(f"[ERROR] Ticket {ticket_id} not found")
        return False

    # Extract timeline
    timeline = parser.extract_timeline(ticket)

    # Convert to emails
    emails = fetcher.convert_ticket_to_emails(ticket)

    # Metric 1: Timeline content length
    total_timeline = sum(len(e.content) for e in timeline)
    print(f"[Metric 1] Timeline content: {total_timeline} chars (target: >1500)")
    assert total_timeline > 1500, f"Timeline too short: {total_timeline}"

    # Metric 2: Email body length
    total_email = sum(len(e.text_body) for e in emails)
    print(f"[Metric 2] Email content: {total_email} chars (target: >2000)")
    assert total_email > 2000, f"Email too short: {total_email}"

    # Metric 3: Content ratio
    ratio = total_timeline / len(ticket.details) if ticket.details else 0
    print(f"[Metric 3] Content ratio: {ratio:.2f} (target: >0.7)")
    assert ratio > 0.7, f"Ratio too low: {ratio}"

    # Show sample content
    print(f"\n[Sample Timeline Entry]")
    if timeline:
        entry = timeline[0]
        print(f"  Action: {entry.action_type}")
        # Safe print for unicode content
        content_preview = entry.content[:200].encode('utf-8', errors='replace').decode('utf-8')
        print(f"  Content preview: {content_preview}...")

    print(f"\n[Sample Email]")
    if emails:
        email = emails[0]
        direction = "=>" if email.is_incoming else "<="
        print(f"  Direction: {direction}")
        body_preview = email.text_body[:200].encode('utf-8', errors='replace').decode('utf-8')
        print(f"  Body preview: {body_preview}...")

    print("\n[OK] All validation checks passed")
    return True

if __name__ == "__main__":
    try:
        validate_fix()
    except AssertionError as e:
        print(f"\n[FAILED] Validation failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
