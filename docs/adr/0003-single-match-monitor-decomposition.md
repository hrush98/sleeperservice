## ADR 0003: Single-Match Live Monitor Decomposition

### Status
Accepted — 2026-02-06

### Context
The live monitor grew into a ~3000-line god-class (legacy `monitor_core.py`, now removed) that handled candidate loading, focus selection, lifecycle inference, polling, WS management, Gamma refresh, snapshot building, trade signals, order execution, and UI state. The root cause of complexity was the system trying to infer which match to watch.

### Decision
Adopt a single-match contract for the live monitor:
- Operator selects the match before the TUI starts.
- The live monitor only polls, renders, and trades that single match.
- Decompose into three focused modules:
  - `poller.py`: one-match polling and snapshot building
  - `trader.py`: edge/trigger evaluation + trade execution
  - `live.py`: thin TUI orchestration and input handling

### Consequences
- Removes candidate lists, focus heuristics, and live/upcoming/ended buckets.
- TUI is simpler and decoupled from trading logic.
- Match switching is explicit via `r` (re-select).
- Discovery remains the only way to see upcoming matches.
