"""RSS feed collection — fetch, deduplicate, filter by recency."""

import hashlib
import re
from datetime import datetime, timezone, timedelta
from time import mktime

import feedparser

from core.constants import PULSE_HOME
from core.logging import log
from core.state import load_json_state, save_json_state


def _article_id(title: str, link: str) -> str:
    """Stable hash for deduplication."""
    return hashlib.md5(f"{title}|{link}".encode()).hexdigest()[:12]


def collect_feeds(config: dict) -> list[dict]:
    """Fetch all configured RSS feeds and return new articles."""
    intel_cfg = config.get("intelligence", {})
    feeds = intel_cfg.get("feeds", [])
    lookback_hours = intel_cfg.get("lookback_hours", 48)
    max_articles = intel_cfg.get("max_articles", 100)
    per_feed_max = intel_cfg.get("per_feed_max", 20)
    state_file = PULSE_HOME / intel_cfg.get("state_file", ".intel-state.json")

    state = load_json_state(state_file, {"seen": {}})
    seen = state.get("seen", {})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    articles = []

    for feed_cfg in feeds:
        url = feed_cfg["url"]
        name = feed_cfg.get("name", url)
        feed_limit = feed_cfg.get("max", per_feed_max)
        log.info(f"  Fetching: {name}...")

        try:
            feed = feedparser.parse(url)
        except Exception as e:
            log.info(f"    ERROR: {e}")
            continue

        count = 0
        for entry in feed.entries:
            if count >= feed_limit:
                break

            # Parse published date
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime.fromtimestamp(
                    mktime(entry.published_parsed), tz=timezone.utc
                )
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime.fromtimestamp(
                    mktime(entry.updated_parsed), tz=timezone.utc
                )

            # Skip old articles
            if published and published < cutoff:
                continue

            title = getattr(entry, "title", "No title")
            link = getattr(entry, "link", "")
            summary = getattr(entry, "summary", "")
            # Strip HTML tags from summary
            if "<" in summary:
                summary = re.sub(r"<[^>]+>", "", summary).strip()

            aid = _article_id(title, link)
            if aid in seen:
                continue

            articles.append({
                "id": aid,
                "title": title,
                "link": link,
                "summary": summary[:500],
                "source": name,
                "published": published.isoformat() if published else "unknown",
            })
            seen[aid] = datetime.now(timezone.utc).isoformat()
            count += 1

        log.info(f"    {count} new articles")

    # Trim to max
    articles = articles[:max_articles]

    # Save state — prune seen entries older than 7 days to prevent unbounded growth
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    seen = {k: v for k, v in seen.items() if v > week_ago}
    state["seen"] = seen
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_json_state(state_file, state)

    return articles
