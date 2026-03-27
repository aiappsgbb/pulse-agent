
## CRM Pipeline Context (optional enrichment)

MSX-MCP tools are available. When triaging items from known customers/accounts:
- Call `msx-mcp-search_accounts` or `msx-mcp-search_opportunities` to add deal context
- Include CRM stage and revenue in the `context` field of triage items
- This helps prioritize: a message from a $5M deal contact is more urgent than a $50K one
- If tool calls fail, skip and continue — CRM context is optional
