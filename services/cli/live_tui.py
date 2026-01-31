"""
Rich TUI rendering for live monitor.
"""

from __future__ import annotations

from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .monitor_types import (
    LogBuffer,
    MatchSnapshot,
    PerfStats,
    format_market_label,
    format_pm_winner,
)


def build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="main", ratio=4),
        Layout(name="logs", size=24),
    )
    layout["logs"].split_column(
        Layout(name="log_stream", ratio=2),
        Layout(name="trade_tape", ratio=2),
        Layout(name="system_logs", ratio=2),
        Layout(name="perf", ratio=1),
    )
    layout["main"].split_column(
        Layout(name="matches", ratio=1),
        Layout(name="window", size=8),
        Layout(name="odds", ratio=3),
        Layout(name="alerts", ratio=1),
    )
    layout["odds"].split_row(
        Layout(name="sharps", ratio=1),
        Layout(name="poly", ratio=1),
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
    alerts_buffer: LogBuffer,
    trade_buffer: LogBuffer,
    log_buffer: LogBuffer,
    system_log_buffer: LogBuffer,
    spread_factor: float,
    perf: PerfStats | None,
) -> None:
    layout["matches"].update(
        Panel(
            _build_matches_table(snapshots, edge_threshold),
            title="Live Matches",
        )
    )
    layout["window"].update(
        Panel(
            _build_window_table(upcoming, recently_ended),
            title="Upcoming / Recently Ended",
        )
    )
    layout["sharps"].update(
        Panel(
            _build_sharps_panel(
                focus_match,
                focus_game1,
                spread_factor,
            ),
            title="Sharps Odds",
        )
    )
    layout["poly"].update(Panel(_build_poly_panel(focus_match, focus_game1), title="Poly Odds"))
    layout["alerts"].update(Panel(_build_alerts_panel(alerts_buffer), title="Alerts"))
    layout["log_stream"].update(Panel(log_buffer.render(), title="Log Stream"))
    layout["trade_tape"].update(Panel(trade_buffer.render(), title="Trade Tape"))
    layout["system_logs"].update(Panel(system_log_buffer.render(), title="System Logs"))
    layout["perf"].update(Panel(_build_perf_panel(perf), title="Performance"))


def _build_matches_table(snapshots: list[MatchSnapshot], edge_threshold: float) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("League", style="cyan", no_wrap=True)
    table.add_column("Match")
    table.add_column("Market", no_wrap=True)
    table.add_column("Start", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Edge", justify="right")
    table.add_column("Side", no_wrap=True)
    table.add_column("Result", no_wrap=True)

    if not snapshots:
        table.add_row("-", "No Matches Live", "-", "-", "-", "-", "-", "-")

    for snap in snapshots:
        edge = snap.edge.best_edge
        edge_str = "-" if edge is None else f"{edge:+.2%}"
        side = snap.edge.best_side or "-"
        start_str = snap.start_time.strftime("%H:%M") if snap.start_time else "--:--"
        market_label = format_market_label(snap.market_type, snap.game_number)
        status_label = snap.pm_resolution_status or "LIVE"
        winner = format_pm_winner(snap.pm_winner)

        style = "bold yellow" if edge is not None and edge >= edge_threshold else ""
        table.add_row(
            snap.league or "-",
            snap.match,
            market_label,
            start_str,
            status_label,
            edge_str,
            side,
            winner,
            style=style,
        )
    return table


def _build_window_table(
    upcoming: list[MatchSnapshot],
    recently_ended: list[MatchSnapshot],
) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Type", no_wrap=True)
    table.add_column("Match")
    table.add_column("Market", no_wrap=True)
    table.add_column("Start", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Result", no_wrap=True)

    if not upcoming and not recently_ended:
        table.add_row("-", "No Upcoming or Recently Ended", "-", "-", "-", "-")
        return table

    for ended in recently_ended[:3]:
        market_label = format_market_label(ended.market_type, ended.game_number)
        status_label = ended.pm_resolution_status or "ENDED"
        winner = format_pm_winner(ended.pm_winner)
        table.add_row(
            "ENDED",
            ended.match,
            market_label,
            "--:--",
            status_label,
            winner,
            style="dim",
        )

    for snap in upcoming[:3]:
        market_label = format_market_label(snap.market_type, snap.game_number)
        status_label = snap.pm_resolution_status or "PRE"
        start_str = snap.start_time.strftime("%H:%M") if snap.start_time else "--:--"
        table.add_row(
            "UPCOMING",
            snap.match,
            market_label,
            start_str,
            status_label,
            "-",
        )
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
