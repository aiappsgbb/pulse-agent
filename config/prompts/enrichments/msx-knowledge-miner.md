## CRM Pipeline Deep Sync (CRM tools available)

CRM tools are available in this session. Execute these additional missions after your core missions.

**CRITICAL — exact tool names (use these EXACTLY, do NOT add extra prefixes):**
- `msx-search_opportunities` — search opportunities by name/keyword
- `msx-search_accounts` — search accounts by name or TPID
- `msx-get_opportunity_details` — full details for a single opportunity
- `msx-get_milestones_for_opportunity` — milestones for a specific deal
- `msx-get_account_deal_teams` — deal team members for an account
- `msx-get_opportunity_solutions` — products on a deal
- `msx-get_my_deals` — ALL your deal team opportunities
- `msx-get_my_milestones` — your milestones across all deals

### Mission A: Link Unlinked Projects to CRM

For each active project WITHOUT an `msx:` block:
1. Call `msx-search_opportunities` with the customer/company name
2. If multiple matches, pick the one closest in name and stage to the project context
3. If no match, try alternate names (e.g., "Contoso" vs "Contoso Ltd" vs "Contoso Inc")

### Mission B: Deep-Enrich Every Linked Project

For each active project WITH an `msx.opportunity_id` (including newly linked ones):
1. Call `msx-get_opportunity_details` — get FULL details: stage, revenue, close date, deal type, solution area, forecast comments, MACC data
2. Call `msx-get_milestones_for_opportunity` — get ALL milestones: number, name, status, date, category, monthly ACR, staleness, commitment
3. Call `msx-get_account_deal_teams` (with the account name) — get deal team roster: names, roles
4. Call `msx-get_opportunity_solutions` — get product line items attached to the opportunity

**Compare and update:**
- If stage changed (e.g., "Qualify" to "Proposal"), update `msx.stage` and add timeline entry: `"[CRM] stage changed from Qualify to Proposal"`
- If revenue changed, update `msx.revenue` and add timeline entry
- If close date moved, update and add timeline entry
- If a milestone status changed (on-track to at-risk), update `msx.milestones` and add timeline entry
- If deal team members changed, update `msx.deal_team`
- If CRM says deal is "closed-lost" but project status is `active`, escalate risk to `critical` and add timeline entry: `"[CRM] deal closed-lost but project still active -- verify"`
- Set `msx.last_synced` to today's date

**Verify deal team membership:**
- Check if YOUR name appears in the deal team roster. Set `msx.in_deal_team: true/false`
- If you're NOT on the deal team for an active project you lead, flag it: `"[CRM] You are NOT on the deal team for this opportunity -- consider joining"`

### Mission C: Deal Portfolio Discovery

Link CRM deals to existing projects and flag untracked deals — but do NOT auto-create projects for every deal.

1. Call `msx-get_my_deals` — get ALL opportunities where you're on the deal team
2. For each deal returned, check if a project file already exists:
   - If a project exists AND already has `msx.opportunity_id` matching — skip (already linked)
   - If a project exists but NO `msx:` block — link it (add `msx:` block with full data)
   - If NO project file exists — **do NOT auto-create**. Instead, log the untracked deal to a discovery summary in the run output. Only create a project file if the deal also meets the standard discovery threshold (3+ mentions across 2+ source types with an actionable element). CRM presence alone is not enough.
3. Also call `msx-get_my_milestones` to find stale milestones across all your deals — add timeline entries to relevant projects that already exist: `"[CRM] milestone {number} is stale ({days} days without update)"`
4. If `get_my_deals` fails, skip this mission entirely

**Why not auto-create?** Deal teams often include dozens of people. Being listed on a deal team doesn't mean you're actively working it. Auto-creating projects for every CRM deal floods the project list with observer-level entries that never get updated. Projects should be created from real activity (meetings, emails, commitments), not CRM roster membership.

### Extended MSX Block Schema

Store all CRM/pipeline data under the `msx:` key in each project YAML. This block is entirely optional -- projects without it work identically.

```yaml
msx:
  opportunity_id: "GUID"
  opportunity_name: "Contoso Enterprise Renewal"
  tpid: "12345"
  account_name: "Contoso Ltd"
  stage: "Proposal"
  revenue: "$2.4M"
  close_date: "2026-04-15"
  deal_type: "New"
  solution_area: "Azure"
  in_deal_team: true
  deal_team:
    - name: "Jane Smith"
      role: "Account Executive"
    - name: "Bob Jones"
      role: "Solution Architect"
  milestones:
    - number: "7-503251276"
      name: "Architecture Review"
      status: "on-track"
      date: "2026-03-15"
      category: "ADS"
      monthly_acr: "$50K"
    - number: "7-503251277"
      name: "PoC Execution"
      status: "at-risk"
      date: "2026-04-01"
      category: "PoC/Pilot"
      monthly_acr: "$120K"
  solutions:
    - product: "Azure OpenAI Service"
      quantity: 1
      amount: "$1.2M"
    - product: "Azure Kubernetes Service"
      quantity: 1
      amount: "$800K"
  forecast_comments: "Customer aligned on architecture. PoC scheduled for March."
  last_synced: "2026-03-27"
```

### Error Handling

If any CRM tool call fails (auth error, timeout, VPN issue), skip that step and continue. Log a timeline entry: `"[CRM] sync failed -- {error reason}"`. Do NOT let CRM failures block your core missions.
