#!/usr/bin/env python3
"""
html_parser.py - Bugtrack HTML export parser for Asteria cases

Extracts ticket data from bugtrack system HTML exports.
Parses table rows to extract AsteriaTicket objects with timeline information.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from bs4 import BeautifulSoup


@dataclass
class TimelineEntry:
    """Single timeline entry from ticket history."""
    timestamp: datetime
    action_type: str  # OPENED, ASSIGNED, EDITED, RESOLVED, CLOSED
    user: str
    content: str


@dataclass
class AsteriaTicket:
    """Asteriaチケットデータ"""
    ticket_id: str           # 853689
    title: str              # 【お問い合わせ対応】MSTeams 送信メッセージサイズ...
    area: str               # Drivers/Teams
    opened: datetime        # 2025/01/07 17:45
    closed: Optional[datetime]  # 2025/01/08 14:44
    priority: str           # Middle
    type: str               # Defect
    importance: str         # Middle
    linked_to: str          # 関連チケットID
    details: str            # HTML形式のやり取り履歴
    raw_html: str           # 元のHTML行


class AsteriaHTMLParser:
    """Parser for bugtrack HTML export files."""

    def __init__(self, html_path: str | Path):
        """
        Initialize parser with HTML file path.

        Args:
            html_path: Path to bugtrack HTML export file
        """
        self.html_path = Path(html_path)
        self._tickets: Optional[List[AsteriaTicket]] = None

    def parse_all_tickets(self) -> List[AsteriaTicket]:
        """
        Parse all tickets from HTML file.

        Returns:
            List of AsteriaTicket objects
        """
        if self._tickets is not None:
            return self._tickets

        with open(self.html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        soup = BeautifulSoup(html_content, "lxml")
        table = soup.find("table")

        if not table:
            raise ValueError("No table found in HTML file")

        tbody = table.find("tbody")
        if not tbody:
            raise ValueError("No tbody found in table")

        rows = tbody.find_all("tr", recursive=False)
        self._tickets = [self._parse_row(row) for row in rows]

        return self._tickets

    def get_ticket_by_id(self, ticket_id: str) -> Optional[AsteriaTicket]:
        """
        Get a specific ticket by ID.

        Args:
            ticket_id: Ticket ID (e.g., "853689")

        Returns:
            AsteriaTicket if found, None otherwise
        """
        tickets = self.parse_all_tickets()
        for ticket in tickets:
            if ticket.ticket_id == ticket_id:
                return ticket
        return None

    def _parse_row(self, row) -> AsteriaTicket:
        """Parse a single table row into AsteriaTicket."""
        cells = row.find_all("td", recursive=False)

        if len(cells) < 10:
            raise ValueError(f"Unexpected row structure: {len(cells)} cells found")

        # Extract basic fields (columns 0-9)
        ticket_id = cells[0].get_text(strip=True)
        title = cells[1].get_text(strip=True)
        area = cells[2].get_text(strip=True)
        opened = self._parse_datetime(cells[3].get_text(strip=True))
        closed_str = cells[4].get_text(strip=True)
        closed = self._parse_datetime(closed_str) if closed_str else None
        priority = cells[5].get_text(strip=True)
        type_ = cells[6].get_text(strip=True)
        importance = cells[7].get_text(strip=True)
        linked_to = cells[8].get_text(strip=True)

        # Details column (column 9) contains the full HTML history
        details_cell = cells[9]
        raw_html = str(details_cell)
        details = self._clean_details_html(raw_html)

        return AsteriaTicket(
            ticket_id=ticket_id,
            title=title,
            area=area,
            opened=opened,
            closed=closed,
            priority=priority,
            type=type_,
            importance=importance,
            linked_to=linked_to,
            details=details,
            raw_html=raw_html,
        )

    def _parse_datetime(self, dt_str: str) -> datetime:
        """
        Parse datetime string from bugtrack format.

        Args:
            dt_str: DateTime string (e.g., "2025/01/07 17:45")

        Returns:
            datetime object
        """
        # Try multiple formats
        formats = [
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d",
            "%Y-%m-%d %H:%M:%S",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(dt_str, fmt)
            except ValueError:
                continue

        raise ValueError(f"Unable to parse datetime: {dt_str}")

    def _clean_details_html(self, html: str) -> str:
        """
        Convert HTML details to readable text while preserving structure.

        - <br>, <p>, <li> → newlines (log readability)
        - Log blocks (code/stack traces) preserved as-is
        - Other HTML tags removed

        Args:
            html: Raw HTML string from details column

        Returns:
            Cleaned text content
        """
        soup = BeautifulSoup(html, "lxml")

        # Replace block elements with newlines before removing
        for tag in soup.find_all(["br", "p", "li"]):
            tag.replace_with("\n")

        # Preserve pre/code blocks (logs, stack traces)
        for pre in soup.find_all(["pre", "code"]):
            pre.replace_with(f"\n```\n{pre.get_text()}\n```\n")

        # Get text and clean up excessive whitespace
        text = soup.get_text()

        # Clean up: multiple newlines → double newline, trailing whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = "\n".join(line.rstrip() for line in text.split("\n"))

        return text

    def extract_timeline(self, ticket: AsteriaTicket) -> List[TimelineEntry]:
        """
        Extract timeline entries from ticket details.

        Parses the HTML details to find OPENED, ASSIGNED, EDITED,
        RESOLVED, CLOSED entries with timestamps and users.

        Args:
            ticket: AsteriaTicket object

        Returns:
            List of TimelineEntry objects in chronological order
        """
        soup = BeautifulSoup(ticket.raw_html, "lxml")
        entries: List[TimelineEntry] = []

        # Pattern: <b>2025/01/07 17:45 OPENED by Megumi Hashimoto</b>
        pattern = re.compile(
            r"(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2})\s+"
            r"(OPENED|ASSIGNED|EDITED|RESOLVED|CLOSED)\s+"
            r"by\s+([^\n<]+)"
        )

        # Find all bold tags containing timeline entries
        for bold in soup.find_all("b"):
            text = bold.get_text(strip=True)
            match = pattern.search(text)

            if match:
                timestamp_str = match.group(1)
                action_type = match.group(2)
                user = match.group(3).strip()

                try:
                    timestamp = self._parse_datetime(timestamp_str)
                except ValueError:
                    continue

                # Get content after this bold tag (until next bold tag)
                content = []
                current = bold.next_sibling  # Start from bold's next sibling

                while current:
                    if hasattr(current, "name"):
                        if current.name == "br":
                            content.append("\n")
                        elif current.name == "b":
                            # Next timeline entry found
                            break
                        elif current.name in ("div", "p", "span", "pre", "code"):
                            # Extract text from DIV elements recursively
                            text = current.get_text()
                            if text.strip():
                                content.append(text)
                    elif current and hasattr(current, "string") and current.string:
                        # Fallback for text nodes
                        text = str(current.string).strip()
                        if text:
                            content.append(text)

                    current = current.next_sibling

                entry = TimelineEntry(
                    timestamp=timestamp,
                    action_type=action_type,
                    user=user,
                    content="".join(content).strip()
                )
                entries.append(entry)

        return entries


def parse_html_file(html_path: str | Path) -> List[AsteriaTicket]:
    """
    Convenience function to parse HTML file and return all tickets.

    Args:
        html_path: Path to bugtrack HTML export file

    Returns:
        List of AsteriaTicket objects
    """
    parser = AsteriaHTMLParser(html_path)
    return parser.parse_all_tickets()


# CLI entry point
if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python -m src.html_parser <html_file> [ticket_id]")
        sys.exit(1)

    html_file = sys.argv[1]
    parser = AsteriaHTMLParser(html_file)

    if len(sys.argv) >= 3:
        # Get specific ticket
        ticket_id = sys.argv[2]
        ticket = parser.get_ticket_by_id(ticket_id)

        if ticket:
            result = {
                "ticket_id": ticket.ticket_id,
                "title": ticket.title,
                "area": ticket.area,
                "opened": ticket.opened.isoformat(),
                "closed": ticket.closed.isoformat() if ticket.closed else None,
                "priority": ticket.priority,
                "type": ticket.type,
                "importance": ticket.importance,
                "linked_to": ticket.linked_to,
                "details_preview": ticket.details[:500] + "..." if len(ticket.details) > 500 else ticket.details,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Ticket {ticket_id} not found")
            sys.exit(1)
    else:
        # List all tickets
        tickets = parser.parse_all_tickets()
        print(f"Found {len(tickets)} tickets:")
        for t in tickets:
            closed_status = f"→ {t.closed.isoformat()}" if t.closed else "(Open)"
            print(f"  {t.ticket_id}: {t.title[:60]}... [{t.opened.isoformat()} {closed_status}]")
