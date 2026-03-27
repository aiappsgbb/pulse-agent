### CRM Pipeline Sync

MSX-MCP is available. After enriching this project from local sources:
1. If project has `msx.opportunity_id`: call `msx-mcp-get_opportunity_details` to refresh stage/revenue/close_date. Call `msx-mcp-get_milestones_for_opportunity` for milestone updates. Call `msx-mcp-get_account_deal_teams` for deal team changes.
2. If no `msx:` block: call `msx-mcp-search_opportunities` with the customer name to find a match
3. Compare CRM data against project state — update `msx:` block and add `[MSX]` timeline entries for any changes
4. Verify deal team membership — set `msx.in_deal_team: true/false`
5. If tool calls fail, skip and continue
