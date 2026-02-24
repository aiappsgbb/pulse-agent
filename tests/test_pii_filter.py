"""Tests for tg/pii_filter — PII scrubbing before Telegram delivery.

Note: EMAIL_ADDRESS is intentionally NOT scrubbed — users need to see
email addresses to verify recipients in outbound message confirmations.
"""

import re
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import tg.pii_filter as pii_mod
from tg.pii_filter import scrub, _scrub_regex, _REGEX_RULES


# --- Regex fallback tests (always available, no Presidio needed) ---

class TestRegexFallback:
    """Test the regex-based PII scrubber directly."""

    def test_email_preserved(self):
        """Emails are intentionally kept for recipient verification."""
        assert _scrub_regex("Contact john@microsoft.com") == "Contact john@microsoft.com"

    def test_email_with_plus_preserved(self):
        assert _scrub_regex("Send to user+tag@example.org") == "Send to user+tag@example.org"

    def test_phone_us(self):
        assert _scrub_regex("Call 555-123-4567") == "Call <phone>"

    def test_phone_international(self):
        assert _scrub_regex("Call +1-555-123-4567") == "Call <phone>"

    def test_phone_with_parens(self):
        assert _scrub_regex("Call (555) 123-4567") == "Call <phone>"

    def test_ip_address(self):
        assert _scrub_regex("Server at 10.0.0.1") == "Server at <ip>"

    def test_credit_card_dashes(self):
        assert _scrub_regex("Card 4111-1111-1111-1111") == "Card <card>"

    def test_credit_card_spaces(self):
        assert _scrub_regex("Card 4111 1111 1111 1111") == "Card <card>"

    def test_credit_card_plain(self):
        assert _scrub_regex("Card 4111111111111111") == "Card <card>"

    def test_iban(self):
        assert _scrub_regex("Account DE89 3704 0044 0532 0130 00") == "Account <iban>"

    def test_no_pii(self):
        text = "The project is on track"
        assert _scrub_regex(text) == text

    def test_keeps_names(self):
        text = "John said the project is on track"
        assert _scrub_regex(text) == text

    def test_keeps_orgs(self):
        text = "Meeting with Contoso about Azure"
        assert _scrub_regex(text) == text

    def test_mixed_pii_keeps_email(self):
        text = "Email john@test.com or call 555-123-4567 from 10.0.0.1"
        result = _scrub_regex(text)
        assert "john@test.com" in result  # email preserved
        assert "<phone>" in result
        assert "<ip>" in result
        assert "555-123-4567" not in result
        assert "10.0.0.1" not in result

    def test_empty_string(self):
        assert _scrub_regex("") == ""

    def test_multiple_emails_preserved(self):
        text = "CC: alice@foo.com and bob@bar.org"
        result = _scrub_regex(text)
        assert "alice@foo.com" in result
        assert "bob@bar.org" in result


# --- scrub() integration tests ---

class TestScrub:
    """Test the main scrub() function (uses whatever backend is available)."""

    def test_empty(self):
        assert scrub("") == ""

    def test_whitespace(self):
        assert scrub("   ") == "   "

    def test_none_safe(self):
        assert scrub("") == ""

    def test_no_pii(self):
        text = "The project is on track for Q3"
        assert scrub(text) == text

    def test_email_preserved(self):
        """Emails must NOT be scrubbed — needed for recipient confirmation."""
        result = scrub("Contact john@microsoft.com for details")
        assert "john@microsoft.com" in result

    def test_phone_scrubbed(self):
        result = scrub("Call +1-202-456-7890")
        assert "202-456-7890" not in result

    def test_credit_card_scrubbed(self):
        result = scrub("Card 4111-1111-1111-1111")
        assert "4111" not in result

    def test_ip_scrubbed(self):
        result = scrub("Server at 192.168.1.1")
        assert "192.168.1.1" not in result


# --- Regex fallback when Presidio is unavailable ---

class TestFallbackBehavior:
    """Ensure scrub() falls back to regex when Presidio is not installed."""

    def test_regex_fallback_on_import_error(self):
        """Force Presidio to be unavailable and verify regex fallback works."""
        pii_mod._presidio_available = None
        pii_mod._analyzer = None
        pii_mod._anonymizer = None

        with mock.patch.dict("sys.modules", {
            "presidio_analyzer": None,
            "presidio_anonymizer": None,
            "presidio_anonymizer.entities": None,
        }):
            pii_mod._presidio_available = None
            result = scrub("Email alice@test.com from 10.0.0.1")
            assert "alice@test.com" in result   # email preserved
            assert "10.0.0.1" not in result
            assert "<ip>" in result

        pii_mod._presidio_available = None

    def test_regex_fallback_on_presidio_exception(self):
        """If Presidio is loaded but throws, fall back to regex."""
        pii_mod._presidio_available = True
        orig_analyzer = pii_mod._analyzer

        pii_mod._analyzer = mock.MagicMock()
        pii_mod._analyzer.analyze.side_effect = RuntimeError("boom")

        result = scrub("Email alice@test.com")
        assert "alice@test.com" in result  # email preserved

        pii_mod._analyzer = orig_analyzer
        pii_mod._presidio_available = None
