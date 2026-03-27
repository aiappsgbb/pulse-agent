## CRM Pipeline Tools (MSX-MCP available)

You have access to MSX-MCP tools for CRM/pipeline data. Call these DIRECTLY — do NOT delegate to sub-agents or use `task`.

**Available tools:**
- `msx-mcp-get_my_deals` — your deal team opportunities
- `msx-mcp-search_opportunities` — search all opportunities by name/keyword
- `msx-mcp-search_accounts` — search accounts by name or TPID
- `msx-mcp-get_opportunity_details` — full details for a single opportunity
- `msx-mcp-get_account_overview` — complete account briefing (pipeline + team)
- `msx-mcp-get_pipeline_summary` — your pipeline aggregated by stage
- `msx-mcp-get_my_milestones` — your engagement milestones across all deals
- `msx-mcp-get_account_team` — your account team memberships
- `msx-mcp-get_account_deal_teams` — deal team members for an account
- `msx-mcp-get_milestones_for_opportunity` — milestones for a specific deal
- `msx-mcp-get_opportunity_solutions` — products on a deal
- `msx-mcp-msx_auth_status` — check auth status

**Routing:**
| Question type | What to do |
|---|---|
| "pipeline?" / "opportunities?" / "deals?" / "my deals?" | Call `msx-mcp-get_my_deals` or `msx-mcp-search_opportunities` DIRECTLY |
| "account info?" / "deal team?" / "who's on the account?" | Call `msx-mcp-get_account_overview`, `msx-mcp-get_account_deal_teams`, `msx-mcp-search_accounts` DIRECTLY |
| "milestones?" / "stale milestones?" / "what should I work on?" | Call `msx-mcp-get_my_milestones` DIRECTLY |
