# ADR: Manual orientation confirmation at focus

**Date:** 2026-02-21  
**Status:** Accepted

## Context

Orientation (mapping Pinnacle home/away to our team_a/team_b) has been fragile: the match market from OddsPapi has no `playerId`, so we relied on game-market IDs or name matching. A global orientation lock (e.g. from discovery or a one-off script) applied to all markets could fix the match but break games (or vice versa), and name-mismatch checks could set CONFLICT and block entry.

## Decision

- When the user **focuses a match** (selects it for the live monitor), we prompt once to **confirm team order**: show Pinnacle order (from OddsPapi fixture: participant1, participant2) and Polymarket order (from PM match fixture: team_a, team_b). User answers [Y]es (orders match), [S]wap Pinnacle, or [C]ancel.
- We store the choice in the mapping’s `match_details`: `orientation_locked=True`, `team_a_is_home` (true = Yes, false = Swap), `orientation_anchor_source="manual_focus_confirm"`. This is used for **all** markets (match winner and game 1, 2, 3) so one consistent mapping applies.
- When `orientation_anchor_source == "manual_focus_confirm"`, we **do not** run the participant name–similarity conflict check in the poller, so we never set `orientation["conflict"] = "locked_name_mismatch"` for manually confirmed mappings. Entry is not blocked for “orientation_conflict” in that case.
- Optional config `orientation_manual_confirm_skip_if_set` (default true): if the mapping already has `manual_focus_confirm`, we skip the prompt on subsequent focus so the user is not re-asked every time.

## Consequences

- Operator can align odds with what they see on Pinnacle and Polymarket without relying on automatic inference or one-off DB scripts.
- One source of truth per mapping; match and game odds stay consistent.
- Existing mappings without manual confirmation continue to use automatic orientation (lock from discovery, game_id_fallback, name matching). Existing one-off manual fixes (e.g. manual_fix_fnatic_vitality) can be replaced by refocusing and confirming with the new flow, which then sets `manual_focus_confirm`.
