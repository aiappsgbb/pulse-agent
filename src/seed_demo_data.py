"""Populate a target PULSE_HOME with mock data for the cross-agent demo.

Usage:
    python scripts/seed_demo_data.py --target-pulse-home /path/to/demo/PulseHome

Idempotent: re-running overwrites the same files with the same content.
"""
from __future__ import annotations

import argparse
from pathlib import Path


TRANSCRIPT_1 = """# Fabric-on-SAP POV, Contoso kickoff

**Date:** 2026-01-15
**Attendees:** Beta Demo (MS), Jordan Sales (Contoso), Alex Architect (Contoso)

## Summary

Kickoff for Fabric-on-SAP POV with Contoso. Jordan flagged licensing complexity
as the #1 objection from their prior attempts. Alex wants a working SAP HANA
to Fabric ingestion demo before committing to a pilot.

## Decisions

- Demo scope: SAP HANA to Fabric OneLake via OpenHub export.
- POV duration: 4 weeks, ending 2026-02-12.
- Target objection answer: total-cost-of-ownership deck addressing licensing.

## Action items

- [Beta Demo] prepare SAP HANA to OneLake walkthrough by 2026-01-22
- [Alex Architect] provide sample HANA dataset by 2026-01-18
- [Jordan Sales] escalate pricing question to Contoso legal

## Quotes

> "We tried this with a competitor last quarter, their licensing math didn't
> hold up in procurement review. That's our biggest risk."
> Jordan Sales
"""

TRANSCRIPT_2 = """# Contoso Fabric-on-SAP follow-up

**Date:** 2026-02-08
**Attendees:** Beta Demo, Jordan Sales, Alex Architect

## Summary

Second demo session. The HANA ingestion walkthrough landed well. Licensing
objection resurfaced, Jordan wants a concrete TCO comparison vs the
competitor (name redacted) before the procurement meeting.

## Decisions

- Beta Demo will produce a 3-year TCO spreadsheet tied to Contoso's workload volume.
- Technical POV declared complete and viable.
- Commercial next step: TCO review on 2026-02-20.

## Key learning

The licensing objection is not about absolute cost, it's about predictability.
Contoso's procurement team burned before on a similar deal where unit counts
scaled unexpectedly. A capped/committed pricing model would likely close it.
"""

EMAIL_1 = """From: Jordan Sales <jordan@contoso.example>
To: Beta Demo <beta.demo@microsoft.example>
Subject: Re: Fabric licensing, follow up
Date: Thu, 20 Feb 2026 14:30:00 +0000

Beta,

Circling back on the licensing question. Our procurement team will need to
see a 3-year commitment option with capped units before we can green-light
the pilot. The per-transaction uncertainty is the blocker, not the headline
price.

Can you get me something by end of next week?

Thanks,
Jordan
"""

PROJECT_YAML = """project: Contoso Fabric-on-SAP
status: active
risk_level: medium
summary: Fabric on SAP HANA POV for Contoso. Technical POV complete, commercial blocked on licensing/TCO clarity.
stakeholders:
  - name: Jordan Sales
    role: Contoso sales lead
  - name: Alex Architect
    role: Contoso technical architect
commitments:
  - what: Deliver 3-year TCO spreadsheet with capped units
    who: Beta Demo
    to: Contoso
    due: 2026-02-27
    status: open
    source: 2026-02-08 follow-up + Jordan email 2026-02-20
next_meeting: 2026-02-27 15:00
key_dates:
  - date: 2026-03-05
    event: Contoso procurement review
"""


FILES = {
    "transcripts/2026-01-15_contoso-fabric-sap-kickoff.md": TRANSCRIPT_1,
    "transcripts/2026-02-08_contoso-fabric-sap-followup.md": TRANSCRIPT_2,
    "emails/2026-02-20_jordan-fabric-licensing.eml": EMAIL_1,
    "projects/contoso-fabric-sap.yaml": PROJECT_YAML,
}


def seed(target: Path) -> int:
    """Populate target with mock files. Returns number of files written."""
    count = 0
    for rel, content in FILES.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo data for cross-agent demo.")
    parser.add_argument(
        "--target-pulse-home", required=True,
        help="Path to the teammate's PULSE_HOME (will be created if missing).",
    )
    args = parser.parse_args()

    target = Path(args.target_pulse_home).resolve()
    target.mkdir(parents=True, exist_ok=True)
    n = seed(target)
    print(f"Seeded {n} files into {target}")


if __name__ == "__main__":
    main()
