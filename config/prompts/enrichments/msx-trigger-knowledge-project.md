### CRM Pipeline Sync

CRM tools are available. After enriching this project from local sources:

**Exact tool names (use EXACTLY as written — prefix is `msx-`):**
- `msx-get_opportunity_details`, `msx-get_milestones_for_opportunity`, `msx-get_account_deal_teams`, `msx-search_opportunities`

1. If project has `msx.opportunity_id`: call `msx-get_opportunity_details` to refresh stage/revenue/close_date. Call `msx-get_milestones_for_opportunity` for milestone updates. Call `msx-get_account_deal_teams` for deal team changes.
2. If no `msx:` block: call `msx-search_opportunities` with the customer name to find a match
3. Compare CRM data against project state — update `msx:` block and add `[CRM]` timeline entries for any changes
4. Verify deal team membership — set `msx.in_deal_team: true/false`
5. If tool calls fail, skip and continue
