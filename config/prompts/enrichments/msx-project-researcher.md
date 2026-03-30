## CRM Pipeline Data (CRM tools available)

CRM tools are available. When creating or updating projects:

**Exact tool names (use EXACTLY as written — prefix is `msx-`):**
- `msx-search_opportunities` — search opportunities by customer name
- `msx-get_account_deal_teams` — check deal team membership

1. Call `msx-search_opportunities` with the customer name to find CRM opportunities
2. If found, add an `msx:` block to the project YAML with: opportunity_id, opportunity_name, stage, close_date, revenue, deal_type, solution_area
3. Call `msx-get_account_deal_teams` to check deal team membership — set `in_deal_team: true/false`
4. If CRM tool calls fail, skip and continue — CRM data is optional enrichment
