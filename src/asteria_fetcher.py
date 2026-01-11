#!/usr/bin/env python3
"""
asteria_fetcher.py - Convert Asteria tickets to EmailMessage format

Converts Asteria ticket timeline data into EmailMessage objects
compatible with the salesforce-case-summarizer pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

try:
    from .html_parser import AsteriaTicket, TimelineEntry, AsteriaHTMLParser
except ImportError:
    from html_parser import AsteriaTicket, TimelineEntry, AsteriaHTMLParser


@dataclass
class EmailMessage:
    """Email message compatible with salesforce-case-summarizer."""
    message_id: str
    subject: str
    from_address: str
    to_address: str
    text_body: str
    message_date: datetime
    is_incoming: bool  # True = customer to support, False = support to customer


class AsteriaFetcher:
    """Convert Asteria tickets to EmailMessage format."""

    # Users considered as "customer" side
    CUSTOMER_USERS = {
        "Megumi Hashimoto",
        "Nao Oki",
        "Emiri Miyamoto",
        "Go Enomoto",
        # Add other Asteria staff names as needed
    }

    # Users considered as "support" side
    SUPPORT_USERS = {
        "CData Japan Support",
    }

    def __init__(self, html_path: str):
        """
        Initialize fetcher with HTML file path.

        Args:
            html_path: Path to bugtrack HTML export file
        """
        self.parser = AsteriaHTMLParser(html_path)

    def fetch_by_ticket_id(self, ticket_id: str) -> List[EmailMessage]:
        """
        Fetch all messages for a ticket.

        Converts timeline entries to EmailMessage format.

        Args:
            ticket_id: Ticket ID (e.g., "853689")

        Returns:
            List of EmailMessage objects in chronological order
        """
        ticket = self.parser.get_ticket_by_id(ticket_id)

        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")

        return self.convert_ticket_to_emails(ticket)

    def convert_ticket_to_emails(self, ticket: AsteriaTicket) -> List[EmailMessage]:
        """
        Convert AsteriaTicket to EmailMessage list.

        Args:
            ticket: AsteriaTicket object

        Returns:
            List of EmailMessage objects
        """
        timeline = self.parser.extract_timeline(ticket)
        emails = self._convert_timeline_to_emails(ticket, timeline)
        return emails

    def _convert_timeline_to_emails(
        self,
        ticket: AsteriaTicket,
        timeline: List[TimelineEntry]
    ) -> List[EmailMessage]:
        """
        Convert timeline entries to EmailMessage objects.

        Conversion rules:
        1. Determine incoming/outgoing by user:
           - Asteria staff → is_incoming=True (customer inquiry)
           - CData Japan Support → is_incoming=False (support response)
        2. Unknown users → is_incoming=False (system note)
        3. Consecutive messages from same side are merged

        Args:
            ticket: AsteriaTicket object
            timeline: List of TimelineEntry objects

        Returns:
            List of EmailMessage objects
        """
        if not timeline:
            return []

        emails: List[EmailMessage] = []
        current_body: List[str] = []
        current_is_incoming: Optional[bool] = None
        current_user: Optional[str] = None
        current_date: Optional[datetime] = None

        for entry in timeline:
            is_incoming = self._is_customer_message(entry.user)

            # First message or direction changed
            if current_is_incoming is None or current_is_incoming != is_incoming:
                # Save previous message if exists
                if current_body and current_is_incoming is not None:
                    email = self._create_email(
                        ticket,
                        current_date or entry.timestamp,
                        current_is_incoming,
                        current_user or "Unknown",
                        "\n".join(current_body)
                    )
                    emails.append(email)

                # Start new message
                current_body = []
                current_is_incoming = is_incoming
                current_user = entry.user
                current_date = entry.timestamp

            # Append content to current message
            if entry.content:
                current_body.append(f"[{entry.action_type}] {entry.content}")
            else:
                current_body.append(f"[{entry.action_type}]")

        # Save last message
        if current_body and current_is_incoming is not None:
            email = self._create_email(
                ticket,
                current_date or timeline[-1].timestamp,
                current_is_incoming,
                current_user or "Unknown",
                "\n".join(current_body)
            )
            emails.append(email)

        return emails

    def _is_customer_message(self, user: str) -> bool:
        """
        Determine if a message is from customer side.

        Args:
            user: User name string

        Returns:
            True if customer (incoming), False if support (outgoing)
        """
        # Extract actual user name (remove "to X" suffix)
        user_clean = re.sub(r"\s+to\s+.+$", "", user.strip())

        if user_clean in self.CUSTOMER_USERS:
            return True  # Customer inquiry
        elif user_clean in self.SUPPORT_USERS:
            return False  # Support response
        else:
            # Unknown user - assume support (system note)
            return False

    def _create_email(
        self,
        ticket: AsteriaTicket,
        date: datetime,
        is_incoming: bool,
        user: str,
        body: str
    ) -> EmailMessage:
        """Create an EmailMessage object."""
        if is_incoming:
            from_addr = user
            to_addr = "CData Japan Support"
        else:
            from_addr = "CData Japan Support"
            to_addr = "Asteria"

        # Clean up user name (remove "to X" suffix)
        user_clean = re.sub(r"\s+to\s+.+$", "", user.strip())

        return EmailMessage(
            message_id=f"{ticket.ticket_id}_{date.isoformat()}_{user_clean.replace(' ', '_')}",
            subject=ticket.title,
            from_address=from_addr,
            to_address=to_addr,
            text_body=body,
            message_date=date,
            is_incoming=is_incoming
        )


# CLI entry point for testing
if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python -m src.asteria_fetcher <html_file> <ticket_id>")
        sys.exit(1)

    html_file = sys.argv[1]

    if len(sys.argv) < 3:
        print("Error: ticket_id required")
        sys.exit(1)

    ticket_id = sys.argv[2]

    fetcher = AsteriaFetcher(html_file)
    emails = fetcher.fetch_by_ticket_id(ticket_id)

    print(f"Found {len(emails)} messages for ticket {ticket_id}:")

    for i, email in enumerate(emails, 1):
        direction = "→" if email.is_incoming else "←"
        print(f"\n{i}. [{direction}] {email.message_date} {email.from_address} -> {email.to_address}")
        print(f"   Subject: {email.subject}")
        print(f"   Body preview: {email.text_body[:200]}...")
