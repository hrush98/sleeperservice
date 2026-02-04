"""
Rich TUI rendering for live monitor.
"""

from __future__ import annotations

from datetime import datetime, timezone

from rich import box
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .monitor_types import (
    LastLogEvents,
    LogBuffer,
    MatchSnapshot,
    PerfStats,
    format_market_label,
    format_pm_winner,
)


def build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="matches", ratio=2),
        Layout(name="focus", ratio=3),
        Layout(name="bottom", size=16),
    )
    layout["bottom"].split_column(
        Layout(name="metrics_row", size=10),
        Layout(name="trade_tape", size=6),
    )
    layout["metrics_row"].split_column(
        Layout(name="metrics_top", size=6),
        Layout(name="last_messages", size=4),
    )
    layout["metrics_top"].split_row(
        Layout(name="perf", size=16),
        Layout(name="logs", ratio=1),
    )
    return layout


def build_log_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="logs", ratio=1),
        Layout(name="last_messages", size=4),
    )
    return layout


def render_layout(
    layout: Layout,
    snapshots: list[MatchSnapshot],
    recently_ended: list[MatchSnapshot],
    upcoming: list[MatchSnapshot],
    focus_match: MatchSnapshot | None,
    focus_game1: MatchSnapshot | None,
    edge_threshold: float,
    trade_buffer: LogBuffer,
    log_buffer: LogBuffer,
    spread_factor: float,
    perf: PerfStats | None,
    ws_connected: bool | None,
    last_events: "LastLogEvents",
) -> None:
    layout["header"].update(
        Panel(
            _build_header(perf, ws_connected, mode_label="DASH"),
            title="Status",
        )
    )
    layout["matches"].update(
        Panel(
            _build_matches_table(snapshots, edge_threshold),
            title="Live Matches",
        )
    )
    layout["focus"].update(
        Panel(
            _build_focus_panel(focus_match, focus_game1, spread_factor),
            title="Focus",
        )
    )
    layout["perf"].update(Panel(_build_perf_meters(perf), title="Performance"))
    layout["logs"].update(Panel(log_buffer.render(), title="Log Stream"))
    layout["last_messages"].update(
        Panel(
            _build_last_messages_panel(last_events),
            title="Last Warn/Error",
        )
    )
    layout["trade_tape"].update(Panel(trade_buffer.render(), title="Trade Tape"))


def render_log_layout(
    layout: Layout,
    log_buffer: LogBuffer,
    perf: PerfStats | None,
    ws_connected: bool | None,
    last_events: "LastLogEvents",
) -> None:
    layout["header"].update(
        Panel(
            _build_header(perf, ws_connected, mode_label="LOGS"),
            title="Status",
        )
    )
    layout["logs"].update(Panel(log_buffer.render(), title="Log Stream"))
    layout["last_messages"].update(
        Panel(
            _build_last_messages_panel(last_events),
            title="Last Warn/Error",
        )
    )


def _build_header(
    perf: PerfStats | None,
    ws_connected: bool | None,
    mode_label: str | None = None,
) -> Table:
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(justify="left")
    table.add_column(justify="center", ratio=1)
    table.add_column(justify="right")

    ws_status = "[green]OK[/]" if ws_connected else "[red]DOWN[/]"
    api_ready = perf is not None and perf.oddspapi_calls > 0
    api_status = "[green]OK[/]" if api_ready else "[yellow]...[/]"
    loop_str = f"{(perf.loop_ms or 0.0) / 1000:.1f}s" if perf and perf.loop_ms else "..."
    time_str = datetime.now(tz=timezone.utc).strftime("%H:%M:%S UTC")

    center_label = "Lead-Lag Monitor"
    if mode_label:
        center_label = f"{center_label} [{mode_label}]"
    table.add_row(
        f"WS: {ws_status} | API: {api_status}",
        center_label,
        f"Loop: {loop_str} | {time_str}",
    )
    return table


def _build_matches_table(snapshots: list[MatchSnapshot], edge_threshold: float) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("League", style="cyan", no_wrap=True)
    table.add_column("Match")
    table.add_column("Market", no_wrap=True)
    table.add_column("Start", no_wrap=True)
    table.add_column("Pin A/B", justify="right", no_wrap=True)
    table.add_column("PM ask A/B", justify="right", no_wrap=True)
    table.add_column("Spr", justify="right", no_wrap=True)

    if not snapshots:
        table.add_row("-", "No Matches Live", "-", "-", "-", "-", "-")

    def _fmt(v: float | None) -> str:
        return "-" if v is None else f"{v:.3f}"

    for snap in snapshots:
        start_str = snap.start_time.strftime("%H:%M") if snap.start_time else "--:--"
        market_label = format_market_label(snap.market_type, snap.game_number)

        pin_ab = f"{_fmt(snap.p_ref_a)}/{_fmt(snap.p_ref_b)}"
        pm_ab = f"{_fmt(snap.ask_a)}/{_fmt(snap.ask_b)}"
        spr_a = (snap.ask_a - snap.bid_a) if (snap.ask_a is not None and snap.bid_a is not None) else None
        spr_b = (snap.ask_b - snap.bid_b) if (snap.ask_b is not None and snap.bid_b is not None) else None
        spr = max([v for v in (spr_a, spr_b) if v is not None], default=None)
        spr_str = "-" if spr is None else f"{max(spr, 0.0):.3f}"

        # Keep a light highlight when the raw gap is large, without showing edge/side columns.
        gap_a = (snap.p_ref_a - snap.ask_a) if (snap.p_ref_a is not None and snap.ask_a is not None) else None
        gap_b = (snap.p_ref_b - snap.ask_b) if (snap.p_ref_b is not None and snap.ask_b is not None) else None
        best_gap = max([v for v in (gap_a, gap_b) if v is not None], default=None)
        style = "bold yellow" if best_gap is not None and best_gap >= edge_threshold else ""
        table.add_row(
            snap.league or "-",
            snap.match,
            market_label,
            start_str,
            pin_ab,
            pm_ab,
            spr_str,
            style=style,
        )
    return table


def _build_perf_meters(perf: PerfStats | None) -> Table:
    from rich.progress_bar import ProgressBar

    table = Table.grid(padding=(0, 1))
    table.add_column("Label", width=10, no_wrap=True)
    table.add_column("Bar", width=6)
    table.add_column("Value", width=8, no_wrap=True)

    # Use per-loop window, not cumulative totals
    api_ms = (
        (perf.oddspapi_ms_window / max(perf.oddspapi_calls_window, 1))
        if perf and perf.oddspapi_calls_window > 0
        else 0.0
    )
    api_pct = min(api_ms / 500.0, 1.0)
    ws_assets = perf.ws_assets if perf else 0
    ws_pct = min(ws_assets / 50.0, 1.0)
    loop_ms = f"{perf.loop_ms:.0f}ms" if perf and perf.loop_ms else "-"

    table.add_row("OddsPapi", ProgressBar(total=1.0, completed=api_pct, width=6), f"{api_ms:.0f}ms")
    table.add_row("WS Books", ProgressBar(total=1.0, completed=ws_pct, width=6), str(ws_assets))
    table.add_row("Loop", Text("-"), loop_ms)
    return table


def _build_focus_panel(
    focus_match: MatchSnapshot | None,
    focus_game1: MatchSnapshot | None,
    spread_factor: float,
) -> Table | Text:
    if not focus_match and not focus_game1:
        return Text("No match selected.")

    table = Table(show_header=True, header_style="bold", box=box.SIMPLE, expand=True)
    table.add_column("Market", no_wrap=True)
    table.add_column("Team", no_wrap=True)
    table.add_column("Pinnacle p_ref", justify="right", no_wrap=True)
    table.add_column("PM ask", justify="right", no_wrap=True)
    table.add_column("PM (bid/ask spr)", justify="right")
    table.add_column("Edge", justify="right", no_wrap=True)

    def _teams(snap: MatchSnapshot) -> tuple[str, str] | None:
        match = (snap.match or "").strip()
        if " vs " in match:
            left, right = match.split(" vs ", 1)
            left = left.strip()
            right = right.strip()
            if left and right:
                return left, right
        return None

    def _append_rows(snap: MatchSnapshot, label: str) -> None:
        teams = _teams(snap)
        a_label = teams[0] if teams else "A"
        b_label = teams[1] if teams else "B"
        for side, p_ref, bid, ask in (
            (a_label, snap.p_ref_a, snap.bid_a, snap.ask_a),
            (b_label, snap.p_ref_b, snap.bid_b, snap.ask_b),
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
                label,
                side,
                p_ref_cell,
                pm_ask_cell,
                Text(pm_detail, style="dim"),
                Text(f"{edge:+.2%}" if edge is not None else "-", style=edge_style),
            )

    if focus_match:
        _append_rows(focus_match, "MATCH (ML)")
    if focus_game1:
        game_label = f"G{focus_game1.game_number}" if focus_game1.game_number else "G"
        table.add_section()
        _append_rows(focus_game1, game_label)
    return table


def _build_sharps_panel(
    focus_match: MatchSnapshot | None,
    focus_game1: MatchSnapshot | None,
    spread_factor: float,
) -> Text:
    text = Text()
    if not focus_match and not focus_game1:
        text.append("No match selected.")
        return text

    header = focus_match.match if focus_match else focus_game1.match
    text.append(f"{header}\n", style="bold")
    _append_sharps_section(text, "Match Winner", focus_match, spread_factor)
    game_label = "Game Winner"
    if focus_game1 and focus_game1.game_number:
        game_label = f"Game {focus_game1.game_number} Winner"
    _append_sharps_section(text, game_label, focus_game1, spread_factor)
    return text


def _build_poly_panel(
    focus_match: MatchSnapshot | None,
    focus_game1: MatchSnapshot | None,
) -> Text:
    text = Text()
    if not focus_match and not focus_game1:
        text.append("No match selected.")
        return text

    header = focus_match.match if focus_match else focus_game1.match
    text.append(f"{header}\n", style="bold")
    _append_poly_section(text, "Match Winner", focus_match)
    game_label = "Game Winner"
    if focus_game1 and focus_game1.game_number:
        game_label = f"Game {focus_game1.game_number} Winner"
    _append_poly_section(text, game_label, focus_game1)
    return text


def _build_alerts_panel(alerts_buffer: LogBuffer) -> Text:
    text = Text()
    if not getattr(alerts_buffer, "_lines", None):
        text.append("No alerts yet.")
        return text
    return alerts_buffer.render()


def _build_last_messages_panel(last_events: LastLogEvents) -> Text:
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column("Label", width=5, no_wrap=True)
    table.add_column("Message", ratio=1)

    warn = last_events.get_last_warning() or "-"
    err = last_events.get_last_error() or "-"

    table.add_row("WARN", Text(warn, style="yellow" if warn != "-" else "dim"))
    table.add_row("ERR", Text(err, style="bold red" if err != "-" else "dim"))
    return table


def _build_perf_panel(perf: PerfStats | None) -> Text:
    text = Text()
    if not perf:
        text.append("No perf data yet.")
        return text
    loop_ms = perf.loop_ms or 0.0
    clob_ms = perf.clob_batch_ms or 0.0
    odds_total = perf.oddspapi_ms_total
    odds_calls = perf.oddspapi_calls
    odds_avg = (odds_total / odds_calls) if odds_calls else 0.0
    gamma_total = perf.gamma_ms_total
    gamma_calls = perf.gamma_calls
    gamma_avg = (gamma_total / gamma_calls) if gamma_calls else 0.0
    text.append(f"Total Loop: {loop_ms/1000:.2f}s\n")
    text.append(
        f"OddsPapi:  {odds_total:.0f}ms ({odds_calls} calls, {odds_avg:.0f}ms avg, "
        f"last batch={perf.oddspapi_last_batch})\n"
    )
    text.append(f"CLOB:      {clob_ms:.0f}ms (fallback books={perf.ws_books_fallback})\n")
    text.append(f"WS Books:  {perf.ws_assets}\n")
    text.append(f"Gamma:     {gamma_total:.0f}ms ({gamma_calls} calls, {gamma_avg:.0f}ms avg)\n")
    return text


def _append_sharps_section(
    text: Text,
    label: str,
    snap: MatchSnapshot | None,
    spread_factor: float,
) -> None:
    text.append(f"\n{label}\n", style="bold")
    if not snap:
        text.append("  unavailable\n")
        return
    _append_compare_focus(text, snap, spread_factor)
    a_odds = f"{snap.odds_a:.2f}" if snap.odds_a is not None else "n/a"
    b_odds = f"{snap.odds_b:.2f}" if snap.odds_b is not None else "n/a"
    a_pref = f"{snap.p_ref_a:.2%}" if snap.p_ref_a is not None else "n/a"
    b_pref = f"{snap.p_ref_b:.2%}" if snap.p_ref_b is not None else "n/a"
    text.append(f"  A odds: {a_odds} | B odds: {b_odds}\n")
    text.append(f"  A p_ref: {a_pref} | B p_ref: {b_pref}\n")
    text.append(f"  Updated: {snap.updated_at.strftime('%H:%M:%S UTC')}\n")


def _append_poly_section(
    text: Text,
    label: str,
    snap: MatchSnapshot | None,
) -> None:
    text.append(f"\n{label}\n", style="bold")
    if not snap:
        text.append("  unavailable\n")
        return
    if snap.pm_resolution_status or snap.pm_winner:
        status = snap.pm_resolution_status or "n/a"
        winner = format_pm_winner(snap.pm_winner)
        text.append(f"  Status: {status} | Winner: {winner}\n")
    text.append(f"  Source: {snap.source_note}\n")
    if snap.bid_a is not None and snap.ask_a is not None:
        spread_a = max(snap.ask_a - snap.bid_a, 0.0)
        text.append(
            f"  A bid/ask: {snap.bid_a:.3f}/{snap.ask_a:.3f} (spr {spread_a:.3f})\n"
        )
    if snap.bid_b is not None and snap.ask_b is not None:
        spread_b = max(snap.ask_b - snap.bid_b, 0.0)
        text.append(
            f"  B bid/ask: {snap.bid_b:.3f}/{snap.ask_b:.3f} (spr {spread_b:.3f})\n"
        )
    text.append(f"  Updated: {snap.updated_at.strftime('%H:%M:%S UTC')}\n")


def _append_compare_focus(text: Text, snap: MatchSnapshot, spread_factor: float) -> None:
    if (
        snap.p_ref_a is not None
        and snap.ask_a is not None
        and snap.bid_a is not None
    ):
        spread_a = max(snap.ask_a - snap.bid_a, 0.0)
        edge_a = snap.p_ref_a - snap.ask_a - (spread_factor * spread_a)
        text.append(
            f"  COMPARE A: {snap.p_ref_a:.3f} vs {snap.ask_a:.3f} -> {edge_a:+.3f}\n",
            style="bold green" if edge_a > 0 else "bold red",
        )
    if (
        snap.p_ref_b is not None
        and snap.ask_b is not None
        and snap.bid_b is not None
    ):
        spread_b = max(snap.ask_b - snap.bid_b, 0.0)
        edge_b = snap.p_ref_b - snap.ask_b - (spread_factor * spread_b)
        text.append(
            f"  COMPARE B: {snap.p_ref_b:.3f} vs {snap.ask_b:.3f} -> {edge_b:+.3f}\n",
            style="bold green" if edge_b > 0 else "bold red",
        )
