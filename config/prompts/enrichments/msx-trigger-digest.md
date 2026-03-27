### CRM Pipeline Enrichment

MSX-MCP tools are available. When creating or updating projects via `update_project`:
1. For NEW projects: call `msx-mcp-search_opportunities` with the customer name
2. If found: include an `msx:` block with opportunity_id, opportunity_name, stage, close_date, revenue, solution_area, deal_type
3. For EXISTING projects with `msx:` block: call `msx-mcp-get_opportunity_details` to verify stage/revenue are current
4. Call `msx-mcp-get_account_deal_teams` to check deal team membership
5. Surface any discrepancies (e.g., project says "active" but CRM says "closed-lost") in the digest
6. If tool calls fail, skip and continue
