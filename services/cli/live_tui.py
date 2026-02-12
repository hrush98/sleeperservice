"""
Rich TUI rendering for single-match live monitor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich import box
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from shared.config import settings
from .monitor_types import FocusSnapshot, LogBuffer, PerfStats, format_market_label


def build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="focus", ratio=3),
        Layout(name="positions", ratio=2),
        Layout(name="trade_tape", size=8),
        Layout(name="logs", size=8),
        Layout(name="input", size=3),
    )
    return layout


def render_layout(
    layout: Layout,
    snapshots: list[FocusSnapshot],
    trade_buffer: LogBuffer,
    log_buffer: LogBuffer,
    spread_factor: float,
    perf: PerfStats | None,
    ws_connected: bool,
    mode_label: str,
    input_text: str,
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
    layout["logs"].update(Panel(log_buffer.render(), title="Logs"))
    layout["input"].update(Panel(_build_input_panel(input_text), title="Command"))


def _build_header(
    snapshot: FocusSnapshot | None,
    perf: PerfStats | None,
    ws_connected: bool,
    mode_label: str,
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

    match_label = snapshot.match if snapshot else "No match selected"
    market_hint = pm_question or ""
    if pm_condition_id:
        market_hint = f"{market_hint} [{pm_condition_id[:10]}…]" if market_hint else pm_condition_id[:10]
    center_label = f"{match_label} | {mode_label.upper()}"
    if market_hint:
        center_label = f"{center_label} | {market_hint}"
    table.add_row(
        f"WS: {ws_status} | API: {api_status}",
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
    table.add_column("Pinnacle p_ref", justify="right", no_wrap=True)
    table.add_column("PM ask", justify="right", no_wrap=True)
    table.add_column("PM (bid/ask spr)", justify="right")
    table.add_column("Edge", justify="right", no_wrap=True)

    for snap_idx, snapshot in enumerate(snapshots):
        if snap_idx > 0:
            # Visual separator between market groups
            table.add_row("", "", "", "", "", "")
        a_label, b_label = _split_match(snapshot.match)
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
            p_ref_cell = Text(f"{p_ref:.3f}" if p_ref is not None else "-", style="bold")
            pm_ask_cell = Text(f"{ask:.3f}" if ask is not None else "-", style="bold")
            pm_detail = "-"
            if bid is not None and ask is not None:
                spr_str = f"{spr:.3f}" if spr is not None else "-"
                pm_detail = f"{bid:.3f}/{ask:.3f} {spr_str}"
            table.add_row(
                format_market_label(snapshot.market_type, snapshot.game_number),
                side_label,
                p_ref_cell,
                pm_ask_cell,
                Text(pm_detail, style="dim"),
                Text(f"{edge:+.2%}" if edge is not None else "-", style=edge_style),
            )
    return table


def _split_match(match: str) -> tuple[str, str]:
    if " vs " in match:
        left, right = match.split(" vs ", 1)
        left = left.strip()
        right = right.strip()
        if left and right:
            return left, right
    return "A", "B"


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


def _build_input_panel(input_text: str) -> Text:
    text = Text()
    prompt = "> "
    display = f"{prompt}{input_text}"
    text.append(display)
    return text
