"""
Rich TUI rendering for single-match live monitor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich import box
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from shared.config import settings
from .monitor_types import FocusSnapshot, LogBuffer, PerfStats, format_market_label

StrategyMode = Literal["lead_lag", "binary", "both"]


def _event_tapes_ratios(strategy_mode: StrategyMode) -> tuple[int, int]:
    """Return (trade_tape_ratio, binary_comp_ratio) from strategy mode."""
    lead_lag_active = strategy_mode in ("lead_lag", "both")
    binary_active = strategy_mode in ("binary", "both")
    if not lead_lag_active and not binary_active:
        return 1, 0
    return (1 if lead_lag_active else 0, 1 if binary_active else 0)


def build_layout(strategy_mode: StrategyMode = "both") -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="focus", ratio=3),
        Layout(name="positions", ratio=2),
        Layout(name="event_tapes", size=8),
        Layout(name="logs", size=8),
        Layout(name="input", size=3),
    )
    r_tape, r_comp = _event_tapes_ratios(strategy_mode)
    layout["event_tapes"].split_row(
        Layout(name="trade_tape", ratio=r_tape),
        Layout(name="binary_comp", ratio=r_comp),
    )
    return layout


def apply_strategy_mode_to_layout(layout: Layout, strategy_mode: StrategyMode) -> None:
    """Update event_tapes row ratios for reselect (same layout object, new mode)."""
    r_tape, r_comp = _event_tapes_ratios(strategy_mode)
    layout["event_tapes"].split_row(
        Layout(name="trade_tape", ratio=r_tape),
        Layout(name="binary_comp", ratio=r_comp),
    )


def render_layout(
    layout: Layout,
    snapshots: list[FocusSnapshot],
    trade_buffer: LogBuffer,
    comp_buffer: LogBuffer,
    log_buffer: LogBuffer,
    spread_factor: float,
    perf: PerfStats | None,
    ws_connected: bool,
    mode_label: str,
    input_text: str,
    paused: bool = False,
    pm_question: str | None = None,
    pm_condition_id: str | None = None,
    positions: list | None = None,
) -> None:
    snapshot = snapshots[0] if snapshots else None
    layout["header"].update(
        Panel(
            _build_header(
                snapshot,
                perf,
                ws_connected,
                mode_label=mode_label,
                paused=paused,
                pm_question=pm_question,
                pm_condition_id=pm_condition_id,
            ),
            title="Status",
        )
    )
    layout["focus"].update(
        Panel(
            _build_focus_panel(snapshots, spread_factor),
            title="Focus",
        )
    )
    layout["positions"].update(
        Panel(_build_positions_panel(positions or []), title="Completed Positions")
    )
    layout["trade_tape"].update(Panel(trade_buffer.render(), title="Trade Tape"))
    layout["binary_comp"].update(Panel(comp_buffer.render(), title="Binary Comp"))
    layout["logs"].update(Panel(log_buffer.render(), title="Logs"))
    layout["input"].update(
        Panel(_build_input_panel(input_text, paused=paused), title="Command")
    )


def _build_header(
    snapshot: FocusSnapshot | None,
    perf: PerfStats | None,
    ws_connected: bool,
    mode_label: str,
    paused: bool = False,
    pm_question: str | None = None,
    pm_condition_id: str | None = None,
) -> Table:
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(justify="left")
    table.add_column(justify="center", ratio=1)
    table.add_column(justify="right")

    ws_status = "[green]OK[/]" if ws_connected else "[red]DOWN[/]"
    api_ready = perf is not None and perf.oddspapi_calls > 0
    api_status = "[green]OK[/]" if api_ready else "[yellow]...[/]"
    loop_ms = f"{perf.loop_ms:.0f}ms" if perf and perf.loop_ms else "..."
    time_str = datetime.now(tz=timezone.utc).strftime("%H:%M:%S UTC")

    left_cell = ""
    if paused:
        left_cell = "[bold red]PAUSED[/] "
    left_cell += f"WS: {ws_status} | API: {api_status}"

    match_label = snapshot.match if snapshot else "No match selected"
    market_hint = pm_question or ""
    if pm_condition_id:
        market_hint = f"{market_hint} [{pm_condition_id[:10]}…]" if market_hint else pm_condition_id[:10]
    center_label = f"{match_label} | {mode_label.upper()}"
    if snapshot:
        orient_state = "LOCKED" if snapshot.orientation_locked else "UNLOCKED"
        orient_source = snapshot.orientation_source or "-"
        conflict_flag = " CONFLICT" if snapshot.orientation_conflict else ""
        center_label = (
            f"{center_label} | ORIENT {orient_state}:{orient_source}{conflict_flag}"
        )
    if market_hint:
        center_label = f"{center_label} | {market_hint}"
    table.add_row(
        left_cell,
        center_label,
        f"Loop: {loop_ms} | {time_str}",
    )
    return table


def _build_focus_panel(snapshots: list[FocusSnapshot], spread_factor: float) -> Table | Text:
    if not snapshots:
        return Text("No match selected.")

    table = Table(show_header=True, header_style="bold", box=box.SIMPLE, expand=True)
    table.add_column("Market", no_wrap=True)
    table.add_column("Team", no_wrap=True)
    table.add_column("p_ref", justify="right", no_wrap=True)
    table.add_column("PM ask", justify="right", no_wrap=True)
    table.add_column("PM (bid/ask spr)", justify="right")
    table.add_column("Edge", justify="right", no_wrap=True)

    for snap_idx, snapshot in enumerate(snapshots):
        if snap_idx > 0:
            # Visual separator between market groups
            table.add_row("", "", "", "", "", "")
        a_label, b_label = _snapshot_side_labels(snapshot)
        for side_label, p_ref, bid, ask in (
            (a_label, snapshot.p_ref_a, snapshot.bid_a, snapshot.ask_a),
            (b_label, snapshot.p_ref_b, snapshot.bid_b, snapshot.ask_b),
        ):
            edge = None
            spr = None
            if p_ref is not None and ask is not None and bid is not None:
                spr = max(ask - bid, 0.0)
                edge = p_ref - ask - (spread_factor * spr)
            edge_style = ""
            if edge is not None:
                edge_style = "bold green" if edge > 0.02 else "bold red" if edge < -0.02 else ""
            p_ref_value = f"{p_ref:.3f}" if p_ref is not None else "-"
            if getattr(snapshot, "p_ref_stale", False):
                p_ref_value = f"{p_ref_value} (stale)" if p_ref_value != "-" else "(stale)"
            p_ref_cell = Text(p_ref_value, style="bold")
            if snapshot.p_ref_source == "derived_series" and p_ref is not None:
                p_ref_cell = Text(f"{p_ref_value}*", style="bold italic")
            pm_ask_cell = Text(f"{ask:.3f}" if ask is not None else "-", style="bold")
            pm_detail = "-"
            if bid is not None and ask is not None:
                spr_str = f"{spr:.3f}" if spr is not None else "-"
                pm_detail = f"{bid:.3f}/{ask:.3f} {spr_str}"
            table.add_row(
                format_market_label(
                    snapshot.market_type,
                    snapshot.game_number,
                    snapshot.line_value,
                    snapshot.p_ref_source,
                ),
                side_label,
                p_ref_cell,
                pm_ask_cell,
                Text(pm_detail, style="dim"),
                Text(f"{edge:+.2%}" if edge is not None else "-", style=edge_style),
            )
    if any(s.p_ref_source == "derived_series" for s in snapshots):
        table.add_row("", "", "", "", "", Text("* derived from series moneyline", style="italic dim"))
    if any(getattr(s, "p_ref_stale", False) for s in snapshots):
        table.add_row("", "", "", "", "", Text("P_ref stale (in-play odds not updated)", style="italic dim"))
    return table


def _split_match(match: str) -> tuple[str, str]:
    if " vs " in match:
        left, right = match.split(" vs ", 1)
        left = left.strip()
        right = right.strip()
        if left and right:
            return left, right
    return "A", "B"


def _snapshot_side_labels(snapshot: FocusSnapshot) -> tuple[str, str]:
    if snapshot.market_type == "totals":
        a = (snapshot.side_a_label or "OVER").strip()
        b = (snapshot.side_b_label or "UNDER").strip()
        return a, b
    return _split_match(snapshot.match)


def _format_ts(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    try:
        tz = ZoneInfo(settings.display_timezone)
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%H:%M:%S")


def _build_positions_panel(positions: list) -> Table | Text:
    """Build a table of recent completed positions."""
    if not positions:
        return Text("No completed positions for this match.", style="dim")
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE, expand=True)
    table.add_column("Market", no_wrap=True)
    table.add_column("Side", no_wrap=True)
    table.add_column("Entry", justify="right", no_wrap=True)
    table.add_column("Qty", justify="right", no_wrap=True)
    table.add_column("Exit", justify="right", no_wrap=True)
    table.add_column("PnL%", justify="right", no_wrap=True)
    table.add_column("Opened", no_wrap=True)
    table.add_column("Closed", no_wrap=True)
    for pos in positions:
        market = format_market_label(pos.market_type, pos.game_number)
        entry = f"{pos.entry_price:.3f}" if pos.entry_price is not None else "-"
        qty = f"{pos.quantity:.2f}" if pos.quantity is not None else "-"
        exit_price = (
            f"{pos.exit_price:.3f}" if pos.exit_price is not None else "-"
        )
        pnl = (
            f"{pos.pnl_percent:+.2f}%" if pos.pnl_percent is not None else "-"
        )
        opened = _format_ts(pos.opened_at)
        closed = _format_ts(pos.closed_at)
        table.add_row(
            market,
            pos.side or "-",
            entry,
            qty,
            exit_price,
            pnl,
            opened,
            closed,
        )
    return table


def _build_input_panel(input_text: str, paused: bool = False) -> Text:
    text = Text()
    if paused:
        text.append("[bold red]PAUSED[/] — ")
    text.append("P=pause/unpause | Q=quit")
    if input_text:
        text.append(f" | > {input_text}")
    return text
