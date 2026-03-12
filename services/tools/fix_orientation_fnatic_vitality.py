#!/usr/bin/env python3
"""
One-off: Lock orientation for the Fnatic vs Team Vitality mapping so MATCH (ML) odds align.

Usage (from repo root, conda env sleeperservice):
  python -m services.tools.fix_orientation_fnatic_vitality
  python -m services.tools.fix_orientation_fnatic_vitality --flip

Finds the mapping for a match with Fnatic and Team Vitality, sets orientation_locked=True
and team_a_is_home so odds line up. Run once; if the display is still wrong, run with --flip.
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from services.shared.db import SessionLocal
from services.shared.models import Fixture, Mapping


def _has_fnatic_vitality(team_a: str | None, team_b: str | None) -> bool:
    def n(s: str | None) -> str:
        return (s or "").lower()
    a, b = n(team_a), n(team_b)
    return ("fnatic" in a or "fnatic" in b) and ("vitality" in a or "vitality" in b)


def main() -> None:
    do_flip = "--flip" in sys.argv
    with SessionLocal() as db:
        mappings = db.execute(select(Mapping)).scalars().all()
        target: Mapping | None = None
        for m in mappings:
            op = db.get(Fixture, m.oddspapi_fixture_id)
            pm = db.get(Fixture, m.polymarket_fixture_id)
            if not op or not pm:
                continue
            if _has_fnatic_vitality(op.team_a_name, op.team_b_name) and _has_fnatic_vitality(
                pm.team_a_name, pm.team_b_name
            ):
                target = m
                break
        if not target:
            print("No mapping found for Fnatic vs Team Vitality. Run discover first or check team names.")
            return
        op = db.get(Fixture, target.oddspapi_fixture_id)
        pm = db.get(Fixture, target.polymarket_fixture_id)
        print(f"Found: Op {op.team_a_name} vs {op.team_b_name} | PM {pm.team_a_name} vs {pm.team_b_name}")
        details = dict(target.match_details) if isinstance(target.match_details, dict) else {}
        current = details.get("team_a_is_home")
        if do_flip:
            team_a_is_home = not current if isinstance(current, bool) else False
        else:
            # First run: set so that we flip the previous wrong display (often was None -> name fallback)
            team_a_is_home = not current if isinstance(current, bool) else True
        details["orientation_locked"] = True
        details["team_a_is_home"] = team_a_is_home
        details["orientation_anchor_source"] = "manual_fix_fnatic_vitality"
        details["home_team"] = op.team_a_name if team_a_is_home else op.team_b_name
        details["away_team"] = op.team_b_name if team_a_is_home else op.team_a_name
        target.match_details = details
        db.commit()
        print(f"Updated: orientation_locked=True, team_a_is_home={team_a_is_home} (--flip={do_flip})")
        print("Refocus the match in live (or restart) for the change to take effect.")


if __name__ == "__main__":
    main()
