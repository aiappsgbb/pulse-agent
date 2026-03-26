Analyze these {{article_count}} articles and generate a SHORT intel brief for {{date}}.

## Watch Topics
{{topics}}

## Competitors
{{competitors}}

## Articles
{{articles}}

## Instructions

Generate an intel brief. MAX 40 lines. Use `write_output` to save as `intel/{{date}}.md`.

Format:

```markdown
# Intel Brief — {{date}}
{{article_count}} articles scanned

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
