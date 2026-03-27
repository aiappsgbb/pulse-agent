### CRM Linking for New Projects

MSX-MCP is available. When discovering new projects:
- Call `msx-mcp-search_opportunities` with the customer name
- If found, include `msx:` block in the project YAML (opportunity_id, name, stage, close_date, revenue, solution_area)
- Call `msx-mcp-get_account_deal_teams` to check deal team membership
- If tool calls fail, skip and continue

### Deal Portfolio Discovery

After archiving, discover deals you're on but don't have project files for:
1. Call `msx-mcp-get_my_deals` to get ALL your deal team opportunities
2. Cross-reference against existing project files — create new projects for untracked deals
3. New projects get `involvement: observer`, `tags: [crm-discovered]`, and a full `msx:` block
4. If `get_my_deals` fails, skip this step
