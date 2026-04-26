## CRM Pipeline Tools Available

You have access to CRM tools for pipeline data. Call these DIRECTLY — do NOT delegate to sub-agents or use `task`.

**CRITICAL — exact tool names (use these EXACTLY, do NOT invent prefixes):**
- `msx-get_my_deals` — your deal team opportunities
- `msx-search_opportunities` — search all opportunities by name/keyword
- `msx-search_accounts` — search accounts by name or TPID
- `msx-get_opportunity_details` — full details for a single opportunity
- `msx-get_account_overview` — complete account briefing (pipeline + team)
- `msx-get_pipeline_summary` — your pipeline aggregated by stage
- `msx-get_my_milestones` — your engagement milestones across all deals
- `msx-get_account_team` — your account team memberships
- `msx-get_account_deal_teams` — deal team members for an account
- `msx-get_milestones_for_opportunity` — milestones for a specific deal
- `msx-get_opportunity_solutions` — products on a deal
- `msx-msx_auth_status` — check auth status

**The prefix is `msx-` — do NOT add extra words like "mcp" between the prefix and tool name. Use the exact names listed above.**

**Routing:**
| Question type | What to do |
|---|---|
| "pipeline?" / "opportunities?" / "deals?" / "my deals?" | Call `msx-get_my_deals` or `msx-search_opportunities` DIRECTLY |
| "account info?" / "deal team?" / "who's on the account?" | Call `msx-get_account_overview`, `msx-get_account_deal_teams`, `msx-search_accounts` DIRECTLY |
| "milestones?" / "stale milestones?" / "what should I work on?" | Call `msx-get_my_milestones` DIRECTLY |
