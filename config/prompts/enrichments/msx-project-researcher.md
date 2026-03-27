## CRM Pipeline Data (MSX-MCP available)

MSX-MCP tools are available. When creating or updating projects:

1. Call `msx-mcp-search_opportunities` with the customer name to find CRM opportunities
2. If found, add an `msx:` block to the project YAML with: opportunity_id, opportunity_name, stage, close_date, revenue, deal_type, solution_area
3. Call `msx-mcp-get_account_deal_teams` to check deal team membership — set `in_deal_team: true/false`
4. If MSX tool calls fail, skip and continue — CRM data is optional enrichment
