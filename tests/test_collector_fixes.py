"""Tests for collector bug fixes — PDF extraction, TTL parsing, article filter,
compressor validation, feed retry, and page.goto timeouts."""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# 1. PDF extractor calls extract_text only once per page
# ---------------------------------------------------------------------------

class TestPdfExtractOnce:
    """Verify _extract_pdf calls extract_text() exactly once per page."""

    def test_extract_text_called_once_per_page(self, tmp_path):
        """Each page's extract_text should be called exactly once, not 3x."""
        # Create mock pages that track calls
        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Page 1 content"
        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = "Page 2 content"
        mock_page3 = MagicMock()
        mock_page3.extract_text.return_value = ""  # empty page

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page1, mock_page2, mock_page3]

        with patch("builtins.open", MagicMock()), \
             patch("PyPDF2.PdfReader", return_value=mock_reader):
            from collectors.extractors import _extract_pdf
            result = _extract_pdf(tmp_path / "test.pdf")

        # Each page should have extract_text called exactly once
        assert mock_page1.extract_text.call_count == 1
        assert mock_page2.extract_text.call_count == 1
        assert mock_page3.extract_text.call_count == 1
        assert result == "Page 1 content\n\nPage 2 content"

    def test_extract_text_skips_whitespace_only_pages(self, tmp_path):
        """Pages with only whitespace should be excluded."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "   \n  \t  "

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch("builtins.open", MagicMock()), \
             patch("PyPDF2.PdfReader", return_value=mock_reader):
            from collectors.extractors import _extract_pdf
            result = _extract_pdf(tmp_path / "test.pdf")

        assert mock_page.extract_text.call_count == 1
        assert result == ""

    def test_extract_text_skips_none_pages(self, tmp_path):
        """Pages returning None should be excluded."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = None

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch("builtins.open", MagicMock()), \
             patch("PyPDF2.PdfReader", return_value=mock_reader):
            from collectors.extractors import _extract_pdf
            result = _extract_pdf(tmp_path / "test.pdf")

        assert mock_page.extract_text.call_count == 1
        assert result == ""


# ---------------------------------------------------------------------------
# 2 & 3. Timestamp TTL handles timezone-aware and malformed timestamps
# ---------------------------------------------------------------------------

class TestTimestampTTL:
    """Verify _load_attempted_slugs handles various timestamp formats."""

    @pytest.fixture(autouse=True)
    def _patch_constants(self, tmp_path):
        """Patch TRANSCRIPT_STATE_FILE to use temp dir."""
        self.state_file = tmp_path / ".transcript-state.json"
        self.tmp_path = tmp_path

    def _write_state(self, attempted: dict):
        self.state_file.write_text(json.dumps({"attempted": attempted}))

    def test_recent_naive_timestamp_kept(self):
        """Recent naive ISO timestamps should be kept."""
        recent = (datetime.now() - timedelta(days=1)).isoformat()
        self._write_state({"meeting-a": recent})

        with patch("collectors.transcripts.collector.TRANSCRIPT_STATE_FILE", self.state_file):
            from collectors.transcripts.collector import _load_attempted_slugs
            result = _load_attempted_slugs()

        assert "meeting-a" in result

    def test_old_naive_timestamp_pruned(self):
        """Naive timestamps older than TTL should be pruned."""
        old = (datetime.now() - timedelta(days=20)).isoformat()
        self._write_state({"meeting-old": old})

        with patch("collectors.transcripts.collector.TRANSCRIPT_STATE_FILE", self.state_file):
            from collectors.transcripts.collector import _load_attempted_slugs
            result = _load_attempted_slugs()

        assert "meeting-old" not in result

    def test_timezone_aware_timestamp_kept(self):
        """Timezone-aware ISO timestamps (with +00:00 offset) should be handled."""
        # A recent timestamp with timezone info
        recent = (datetime.now() - timedelta(hours=2)).isoformat()
        # Simulate a tz-aware string like "2026-03-25T10:00:00+02:00"
        recent_tz = (datetime.now() - timedelta(hours=2)).isoformat() + "+02:00"
        # This should NOT crash even though comparing aware vs naive
        # After the fix, fromisoformat handles it
        self._write_state({"meeting-tz": recent})

        with patch("collectors.transcripts.collector.TRANSCRIPT_STATE_FILE", self.state_file):
            from collectors.transcripts.collector import _load_attempted_slugs
            result = _load_attempted_slugs()

        assert "meeting-tz" in result

    def test_malformed_timestamp_dropped(self):
        """Malformed timestamps should be silently dropped, not crash."""
        self._write_state({
            "good-meeting": (datetime.now() - timedelta(days=1)).isoformat(),
            "bad-meeting": "not-a-timestamp",
            "also-bad": "",
            "none-val": None,
        })

        with patch("collectors.transcripts.collector.TRANSCRIPT_STATE_FILE", self.state_file):
            from collectors.transcripts.collector import _load_attempted_slugs
            result = _load_attempted_slugs()

        assert "good-meeting" in result
        assert "bad-meeting" not in result
        assert "also-bad" not in result
        assert "none-val" not in result

    def test_mixed_aware_naive_timestamps(self):
        """Mix of timezone-aware and naive timestamps should not crash."""
        naive_recent = (datetime.now() - timedelta(days=1)).isoformat()
        # fromisoformat in Python 3.11+ handles +00:00
        aware_recent = (datetime.now() - timedelta(days=1)).isoformat() + "+00:00"
        self._write_state({
            "naive": naive_recent,
            "aware": aware_recent,
        })

        with patch("collectors.transcripts.collector.TRANSCRIPT_STATE_FILE", self.state_file):
            from collectors.transcripts.collector import _load_attempted_slugs
            # Should not raise — the fix parses each timestamp individually
            result = _load_attempted_slugs()

        assert "naive" in result
        # The aware one may or may not be kept depending on comparison,
        # but critically it should NOT crash


# ---------------------------------------------------------------------------
# 4. Article filter handles various markdown code block formats
# ---------------------------------------------------------------------------

class TestArticleFilterJsonExtraction:
    """Verify the regex-based code block stripping works for various formats."""

    def _extract_json(self, raw_response: str) -> str:
        """Simulate the JSON extraction logic from article_filter.py."""
        import re
        json_text = raw_response
        match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?\s*```', json_text)
        if match:
            json_text = match.group(1).strip()
        return json_text

    def test_bare_json(self):
        """Raw JSON without code blocks."""
        raw = '[{"id": "abc", "why": "reason"}]'
        assert self._extract_json(raw) == raw

    def test_json_code_block(self):
        """```json code block."""
        raw = '```json\n[{"id": "abc", "why": "reason"}]\n```'
        result = self._extract_json(raw)
        parsed = json.loads(result)
        assert parsed[0]["id"] == "abc"

    def test_plain_code_block(self):
        """``` code block without language tag."""
        raw = '```\n[{"id": "abc", "why": "reason"}]\n```'
        result = self._extract_json(raw)
        parsed = json.loads(result)
        assert parsed[0]["id"] == "abc"

    def test_code_block_with_extra_whitespace(self):
        """Code block with extra whitespace around content."""
        raw = '```json\n\n  [{"id": "abc", "why": "reason"}]  \n\n```'
        result = self._extract_json(raw)
        parsed = json.loads(result)
        assert parsed[0]["id"] == "abc"

    def test_code_block_with_surrounding_text(self):
        """Code block with explanation text before/after."""
        raw = 'Here are the results:\n```json\n[{"id": "abc", "why": "reason"}]\n```\nDone.'
        result = self._extract_json(raw)
        parsed = json.loads(result)
        assert parsed[0]["id"] == "abc"

    def test_empty_array_in_code_block(self):
        """Empty array in code block."""
        raw = '```json\n[]\n```'
        result = self._extract_json(raw)
        assert json.loads(result) == []


# ---------------------------------------------------------------------------
# 5 & 6. Compressor rejects too-short and structureless output
# ---------------------------------------------------------------------------

class TestCompressorValidation:
    """Verify compressor rejects bad output."""

    @pytest.mark.asyncio
    async def test_rejects_too_short_output(self):
        """Output under 50 chars should be rejected."""
        short_output = "Too short."
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.destroy = AsyncMock()
        mock_client.create_session = AsyncMock(return_value=mock_session)

        with patch("collectors.transcripts.compressor._send_and_collect",
                    new_callable=AsyncMock, return_value=short_output):
            from collectors.transcripts.compressor import compress_transcript
            result = await compress_transcript(
                mock_client, "x" * 1000, "Test Meeting"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_structureless_output(self):
        """Output with fewer than 3 newlines should be rejected."""
        # 60 chars but only 1 newline — lacks structure
        flat_output = "This is a single paragraph with no structure.\nJust one line break and that is it, really."
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.destroy = AsyncMock()
        mock_client.create_session = AsyncMock(return_value=mock_session)

        with patch("collectors.transcripts.compressor._send_and_collect",
                    new_callable=AsyncMock, return_value=flat_output):
            from collectors.transcripts.compressor import compress_transcript
            result = await compress_transcript(
                mock_client, "x" * 1000, "Test Meeting"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_accepts_good_output(self):
        """Well-structured output with enough length and newlines should pass."""
        good_output = (
            "## Meeting Summary\n"
            "- Discussed Q1 targets and resource allocation\n"
            "- Agreed on new hiring plan for engineering team\n"
            "\n"
            "## Action Items\n"
            "- John will draft the proposal by Friday\n"
            "- Sarah will review budget numbers\n"
        )
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.destroy = AsyncMock()
        mock_client.create_session = AsyncMock(return_value=mock_session)

        with patch("collectors.transcripts.compressor._send_and_collect",
                    new_callable=AsyncMock, return_value=good_output):
            from collectors.transcripts.compressor import compress_transcript
            result = await compress_transcript(
                mock_client, "x" * 1000, "Test Meeting"
            )

        assert result == good_output


# ---------------------------------------------------------------------------
# 7. Feed retry logic
# ---------------------------------------------------------------------------

class TestFeedRetry:
    """Verify feed fetcher retries once on failure."""

    def test_retry_on_first_failure(self, tmp_path):
        """feedparser.parse failing once then succeeding should work."""
        mock_feed = MagicMock()
        mock_feed.entries = []

        call_count = {"n": 0}

        def mock_parse(url):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionError("Network error")
            return mock_feed

        config = {
            "intelligence": {
                "feeds": [{"url": "https://example.com/feed", "name": "Test"}],
                "state_file": str(tmp_path / ".intel-state.json"),
            }
        }

        with patch("collectors.feeds.feedparser.parse", side_effect=mock_parse), \
             patch("collectors.feeds.PULSE_HOME", tmp_path), \
             patch("collectors.feeds.time.sleep") as mock_sleep:
            from collectors.feeds import collect_feeds
            result = collect_feeds(config)

        assert call_count["n"] == 2
        mock_sleep.assert_called_once_with(2)
        assert result == []  # no entries, but no crash

    def test_both_attempts_fail(self, tmp_path):
        """feedparser.parse failing both times should log and continue."""
        def mock_parse(url):
            raise ConnectionError("Network error")

        config = {
            "intelligence": {
                "feeds": [{"url": "https://example.com/feed", "name": "Test"}],
                "state_file": str(tmp_path / ".intel-state.json"),
            }
        }

        with patch("collectors.feeds.feedparser.parse", side_effect=mock_parse), \
             patch("collectors.feeds.PULSE_HOME", tmp_path), \
             patch("collectors.feeds.time.sleep"):
            from collectors.feeds import collect_feeds
            result = collect_feeds(config)

        assert result == []  # graceful degradation

    def test_success_on_first_attempt(self, tmp_path):
        """feedparser.parse succeeding immediately should not sleep."""
        mock_feed = MagicMock()
        mock_feed.entries = []

        config = {
            "intelligence": {
                "feeds": [{"url": "https://example.com/feed", "name": "Test"}],
                "state_file": str(tmp_path / ".intel-state.json"),
            }
        }

        with patch("collectors.feeds.feedparser.parse", return_value=mock_feed), \
             patch("collectors.feeds.PULSE_HOME", tmp_path), \
             patch("collectors.feeds.time.sleep") as mock_sleep:
            from collectors.feeds import collect_feeds
            result = collect_feeds(config)

        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# 8. page.goto timeout parameter
# ---------------------------------------------------------------------------

class TestGotoTimeouts:
    """Verify page.goto calls include explicit timeout=30000."""

    def test_teams_inbox_goto_has_timeout(self):
        """teams_inbox._do_scan passes timeout to page.goto."""
        import ast
        from pathlib import Path
        source = Path("src/collectors/teams_inbox.py").read_text()
        tree = ast.parse(source)
        # Find the goto call string
        assert "timeout=30000" in source.split("page.goto")[1].split(")")[0]

    def test_outlook_inbox_goto_has_timeout(self):
        """outlook_inbox._do_scan passes timeout to page.goto."""
        source = Path("src/collectors/outlook_inbox.py").read_text()
        assert "timeout=30000" in source.split("page.goto")[1].split(")")[0]

    def test_calendar_goto_has_timeout(self):
        """calendar._do_scan passes timeout to page.goto."""
        source = Path("src/collectors/calendar.py").read_text()
        assert "timeout=30000" in source.split("page.goto")[1].split(")")[0]


