"""External intelligence — collects RSS feeds and generates a daily intel brief.

Two-phase approach (mirrors digest.py):
1. Collection: Fetch RSS feeds, deduplicate, filter by recency
2. Analysis: Send articles to GHCP SDK agent for structured summarization
"""

import json
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from time import mktime

import feedparser

from copilot import CopilotClient

from session import PROJECT_ROOT, OUTPUT_DIR
from tools import get_tools
from utils import agent_session, log


# --- Phase 1: RSS Collection ---

def _load_intel_state(state_file: Path) -> dict:
    if state_file.exists():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {"seen": {}}


def _save_intel_state(state_file: Path, state: dict):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _article_id(title: str, link: str) -> str:
    """Stable hash for deduplication."""
    return hashlib.md5(f"{title}|{link}".encode()).hexdigest()[:12]


def collect_feeds(config: dict) -> list[dict]:
    """Fetch all configured RSS feeds and return new articles."""
    intel_cfg = config.get("intelligence", {})
    feeds = intel_cfg.get("feeds", [])
    lookback_hours = intel_cfg.get("lookback_hours", 48)
    max_articles = intel_cfg.get("max_articles", 100)
    state_file = PROJECT_ROOT / intel_cfg.get("state_file", "output/.intel-state.json")

    state = _load_intel_state(state_file)
    seen = state.get("seen", {})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    articles = []

    for feed_cfg in feeds:
        url = feed_cfg["url"]
        name = feed_cfg.get("name", url)
        log.info(f"  Fetching: {name}...")

        try:
            feed = feedparser.parse(url)
        except Exception as e:
            log.info(f"    ERROR: {e}")
            continue

        count = 0
        for entry in feed.entries:
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
                import re
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

    # Save state
    # Prune seen entries older than 7 days to prevent unbounded growth
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    seen = {k: v for k, v in seen.items() if v > week_ago}
    state["seen"] = seen
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    _save_intel_state(state_file, state)

    return articles


# --- Phase 2: LLM Analysis ---

async def run_intel(client: CopilotClient, config: dict):
    """Run a full intel cycle: collect feeds → analyze → write intel brief."""
    log.info("\n=== Intel cycle start ===")

    # Phase 1: Collect
    log.info("Phase 1: Fetching RSS feeds...")
    articles = collect_feeds(config)

    if not articles:
        log.info("  No new articles. Intel cycle complete.")
        log.info("=== Intel cycle end ===")
        return

    log.info(f"  Collected {len(articles)} new articles")

    # Phase 2: Analyze
    log.info("Phase 2: Sending to agent for analysis...")

    async with agent_session(client, config, "intel", tools=get_tools()) as session:
        prompt = _build_intel_prompt(articles, config)
        log.info(f"  Prompt size: {len(prompt)} chars")
        log.info("  Agent working...")

        response = await session.send_and_wait({"prompt": prompt}, timeout=300)

        if not response:
            log.warning("No response from agent (timed out).")

    log.info("=== Intel cycle end ===")


def _build_intel_prompt(articles: list[dict], config: dict) -> str:
    date_str = datetime.now().strftime("%Y-%m-%d")
    intel_cfg = config.get("intelligence", {})
    topics = intel_cfg.get("topics", [])
    competitors = intel_cfg.get("competitors", [])

    topics_str = ", ".join(topics)
    competitors_str = "\n".join(
        f"- **{c['company']}**: watching {', '.join(c['watch'])}"
        for c in competitors
    )

    # Build article list
    article_lines = []
    for a in articles:
        article_lines.append(
            f"- [{a['source']}] **{a['title']}**\n"
            f"  Link: {a['link']}\n"
            f"  Published: {a['published']}\n"
            f"  Summary: {a['summary']}"
        )
    articles_block = "\n\n".join(article_lines)

    return f"""Analyze these {len(articles)} articles and generate a SHORT intel brief for {date_str}.

## Watch Topics
{topics_str}

## Competitors
{competitors_str}

## Articles
{articles_block}

## Instructions

Generate an intel brief. MAX 40 lines. Use `write_output` to save as `intel/{date_str}.md`.

Format:

```markdown
# Intel Brief — {date_str}
{len(articles)} articles scanned

## Moves & Announcements
- **[Company]** — what happened — why it matters to us (1 line each)

## Trends
- Key patterns across multiple articles (2-3 bullets max)

## Watch List
- Anything that could affect our competitive positioning or customer conversations
```

CRITICAL:
- Only include articles that are actually relevant to the watch topics/competitors.
- Skip generic AI hype articles with no substance.
- Be specific — names, products, pricing, dates.
- If nothing significant happened, say so. Don't pad.
- Use `log_action` to log your analysis.
"""


