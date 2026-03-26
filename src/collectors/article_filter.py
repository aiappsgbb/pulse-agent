"""Pre-filter RSS articles via GHCP SDK before they reach digest/intel agents.

Sends raw article list through a lightweight SDK session that scores relevance
and returns only articles worth surfacing, each with a 1-line "why it matters".
Falls back to unfiltered articles if the SDK call fails.
"""

import asyncio
import json
import re

from copilot import CopilotClient

from core.logging import log, safe_encode
from sdk.session import auto_approve_handler

FILTER_PROMPT = """\
You are an article relevance filter for an enterprise AI consultant. Your job is to \
read a batch of article headlines + summaries and decide which ones are actually \
worth a busy person's time.

KEEP articles that are:
- Concrete product launches, pricing changes, or capability announcements
- Competitive moves (AWS, Google Cloud, Salesforce, Anthropic, OpenAI, Databricks, Meta)
- Significant research breakthroughs (not incremental papers)
- Enterprise AI adoption stories with real numbers
- Regulatory or policy changes affecting AI deployment
- Open-source releases that shift the landscape

DROP articles that are:
- Generic "AI is changing everything" hype with no specifics
- Listicles, opinion pieces, or thought leadership fluff
- Duplicate stories (same news from different outlets — keep the best one)
- Minor updates, patch notes, or routine announcements
- Academic papers with no practical near-term impact
- Hiring posts, self-promotion threads, or meta-discussions

Respond with ONLY a JSON array. Each element:
{"id": "<article id>", "why": "<1 line — why this matters, be specific>"}

If nothing is worth keeping, return an empty array: []
Do NOT wrap in markdown code blocks. Return raw JSON only.
"""


async def filter_articles(
    client: CopilotClient,
    articles: list[dict],
    topics: list[str] | None = None,
    competitors: list[dict] | None = None,
    model: str = "gpt-4.1",
) -> tuple[list[dict], bool]:
    """Filter articles via SDK, keeping only the relevant ones.

    Args:
        client: GHCP SDK client (already started)
        articles: Raw articles from collect_feeds()
        topics: Watch topics from config
        competitors: Competitor watch list from config
        model: Model to use for filtering

    Returns:
        Tuple of (articles, was_filtered). was_filtered=False means the filter
        failed and all unfiltered articles are returned — callers should warn.
    """
    if not articles:
        return [], True

    log.info(f"  Filtering {len(articles)} articles via SDK...")

    # Build context about what matters
    context_parts = []
    if topics:
        context_parts.append(f"Watch topics: {', '.join(topics)}")
    if competitors:
        comp_lines = [f"- {c['company']}: {', '.join(c.get('watch', []))}" for c in competitors]
        context_parts.append("Competitors:\n" + "\n".join(comp_lines))
    extra_context = "\n".join(context_parts)

    # Format articles for the prompt
    article_lines = []
    for a in articles:
        article_lines.append(
            f"[{a['id']}] [{a['source']}] {a['title']}\n"
            f"  {a['summary'][:300]}"
        )
    articles_text = "\n\n".join(article_lines)

    prompt = (
        f"Filter these {len(articles)} articles.\n\n"
        f"## Context\n{extra_context}\n\n"
        f"## Articles\n{articles_text}"
    )

    session = None
    try:
        session = await client.create_session({
            "model": model,
            "system_message": {"mode": "replace", "content": FILTER_PROMPT},
            "streaming": True,
            "on_permission_request": auto_approve_handler,
        })

        from sdk.event_handler import EventHandler
        handler = EventHandler()
        unsub = session.on(handler)

        await session.send({"prompt": prompt})

        try:
            await asyncio.wait_for(handler.done.wait(), timeout=120)
        except asyncio.TimeoutError:
            log.warning("  Article filter timed out — using unfiltered articles")
            return articles, False

        if handler.error:
            log.warning(f"  Article filter error: {handler.error}")
            return articles, False

        raw_response = (handler.final_text or "").strip()
        if not raw_response:
            log.warning("  Article filter returned empty — using unfiltered articles")
            return articles, False

        # Parse JSON — strip markdown code blocks if the model wraps them
        json_text = raw_response
        match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?\s*```', json_text)
        if match:
            json_text = match.group(1).strip()

        kept_items = json.loads(json_text)
        if not isinstance(kept_items, list):
            log.warning("  Article filter returned non-list — using unfiltered articles")
            return articles, False

        # Build lookup of kept IDs -> why
        kept_map = {item["id"]: item.get("why", "") for item in kept_items if "id" in item}

        # Filter and annotate
        filtered = []
        for a in articles:
            if a["id"] in kept_map:
                a["why"] = kept_map[a["id"]]
                filtered.append(a)

        log.info(f"  Filtered: {len(articles)} -> {len(filtered)} articles ({len(articles) - len(filtered)} dropped)")
        return filtered, True

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning(f"  Article filter parse error: {e} — using unfiltered articles")
        return articles, False
    except Exception as e:
        log.warning(f"  Article filter failed: {e} — using unfiltered articles")
        return articles, False
    finally:
        if session:
            try:
                await session.destroy()
            except Exception:
                pass
