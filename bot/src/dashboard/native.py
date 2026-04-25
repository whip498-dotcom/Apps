"""Native Tkinter dashboard. Zero external dependencies — Tkinter ships
with Python, no WebView2, no Chromium, no Flask required.

Reads `data_cache/dashboard_state.json` directly (the scanner writes it
each cycle) and re-renders every 3 seconds. Resizable native window,
optional always-on-top, OS-native title bar and taskbar entry.

Run:
    python -m src.cli dashboard-app                  # default: always-on-top
    python -m src.cli dashboard-app --no-always-on-top
"""
from __future__ import annotations

import tkinter as tk
import webbrowser
from datetime import datetime, timezone
from tkinter import ttk
from tkinter.font import Font

from .state import read_state

# Color palette (matches the Discord embed scheme)
BG = "#0e1116"
PANEL = "#161b22"
PANEL2 = "#1c222b"
BORDER = "#30363d"
TEXT = "#e6edf3"
MUTED = "#8b949e"
GOLD = "#f1c40f"
GREEN = "#2ecc71"
RED = "#e74c3c"
BLUE = "#3498db"
ORANGE = "#e67e22"
PURPLE = "#9b59b6"


def _fmt_money(v) -> str:
    return f"${float(v):.2f}" if v is not None else "—"


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{float(v):.1f}%"


def _fmt_num(v) -> str:
    return f"{int(v):,}" if v is not None else "—"


def _fmt_float(v) -> str:
    if v is None:
        return "?"
    return f"{float(v) / 1_000_000:.1f}M"


def _rel_time(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "—"
    ago = (datetime.now(timezone.utc) - ts).total_seconds()
    if ago < 60:
        return f"{int(ago)}s ago"
    if ago < 3600:
        return f"{int(ago / 60)}m ago"
    return f"{int(ago / 3600)}h ago"


class Dashboard:
    REFRESH_MS = 3000

    def __init__(self, root: tk.Tk, always_on_top: bool = True):
        self.root = root
        self.root.title("EdgeHawk")
        self.root.geometry("1400x900")
        self.root.minsize(900, 650)
        self.root.configure(bg=BG)
        self.on_top_var = tk.BooleanVar(value=always_on_top)
        self.root.attributes("-topmost", always_on_top)

        self._init_fonts()
        self._init_style()
        self._init_menu()
        self._build_layout()
        self._refresh()

    # --- setup ------------------------------------------------------------

    def _init_fonts(self) -> None:
        self.font_h1 = Font(family="Segoe UI", size=14, weight="bold")
        self.font_h2 = Font(family="Segoe UI", size=11, weight="bold")
        self.font_body = Font(family="Segoe UI", size=10)
        self.font_mono = Font(family="Consolas", size=10)
        self.font_big_sym = Font(family="Segoe UI", size=22, weight="bold")
        self.font_score = Font(family="Segoe UI", size=18, weight="bold")
        self.font_label = Font(family="Segoe UI", size=9)

    def _init_style(self) -> None:
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TEXT, fieldbackground=BG, borderwidth=0)
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("Panel2.TFrame", background=PANEL2)
        s.configure("TLabel", background=BG, foreground=TEXT, font=self.font_body)
        s.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=self.font_body)
        s.configure("Panel2.TLabel", background=PANEL2, foreground=TEXT, font=self.font_body)
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=self.font_label)
        s.configure("Muted2.TLabel", background=PANEL2, foreground=MUTED, font=self.font_label)
        s.configure("Header.TLabel", background=PANEL, foreground=TEXT, font=self.font_h1)
        s.configure("CardTitle.TLabel", background=PANEL, foreground=MUTED, font=self.font_h2)
        s.configure("Mono.TLabel", background=PANEL, foreground=TEXT, font=self.font_mono)
        s.configure("Mono2.TLabel", background=PANEL2, foreground=TEXT, font=self.font_mono)

        # Treeview (candidates table)
        s.configure("Cands.Treeview",
                    background=PANEL,
                    fieldbackground=PANEL,
                    foreground=TEXT,
                    rowheight=26,
                    font=self.font_mono,
                    bordercolor=BORDER,
                    borderwidth=0)
        s.configure("Cands.Treeview.Heading",
                    background=PANEL2,
                    foreground=MUTED,
                    relief="flat",
                    font=self.font_label)
        s.map("Cands.Treeview.Heading", background=[("active", PANEL2)])
        s.map("Cands.Treeview",
              background=[("selected", "#1f6feb")],
              foreground=[("selected", TEXT)])

    def _init_menu(self) -> None:
        menubar = tk.Menu(self.root)
        view = tk.Menu(menubar, tearoff=0)
        view.add_checkbutton(label="Always on top", variable=self.on_top_var,
                             command=self._toggle_on_top)
        view.add_command(label="Refresh now", command=self._refresh)
        menubar.add_cascade(label="View", menu=view)
        self.root.config(menu=menubar)

    def _toggle_on_top(self) -> None:
        self.root.attributes("-topmost", self.on_top_var.get())

    # --- layout -----------------------------------------------------------

    def _build_layout(self) -> None:
        # Header bar
        header = tk.Frame(self.root, bg=PANEL, height=50)
        header.pack(fill="x")
        header.pack_propagate(False)

        ttk.Label(header, text="🦅 EdgeHawk",
                  style="Header.TLabel").pack(side="left", padx=14)

        self.window_pill = ttk.Label(header, text="--", style="Mono.TLabel")
        self.window_pill.pack(side="left", padx=8)

        self.window_meta = ttk.Label(header, text="", style="Muted.TLabel")
        self.window_meta.pack(side="left", padx=4)

        self.conv_pill = ttk.Label(header, text="--", style="Mono.TLabel")
        self.conv_pill.pack(side="left", padx=8)

        self.updated_label = ttk.Label(header, text="—", style="Muted.TLabel")
        self.updated_label.pack(side="right", padx=14)

        # Scrollable main area
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.body = tk.Frame(canvas, bg=BG)
        self._body_window = canvas.create_window((0, 0), window=self.body, anchor="nw")

        def _on_configure(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(self._body_window, width=canvas.winfo_width())

        self.body.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", _on_configure)
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"),
        )

        # Top pick card
        self.top_card = self._make_card(self.body, "🥇 Top Pick", border_color=GOLD)
        self.top_card_inner = tk.Frame(self.top_card, bg=PANEL)
        self.top_card_inner.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        # Candidates table
        cands_card = self._make_card(self.body, "📋 Ranked Candidates")
        cands_inner = tk.Frame(cands_card, bg=PANEL)
        cands_inner.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        cols = ("rank", "sym", "side", "conv", "setup", "price", "gap",
                "rvol", "rot", "float", "entry", "stop", "tp1", "rr", "score")
        headings = {
            "rank": ("#", 32), "sym": ("Symbol", 90), "side": ("Side", 60),
            "conv": ("Conv", 60), "setup": ("Setup", 130),
            "price": ("Price", 70), "gap": ("Gap%", 64), "rvol": ("RVol", 60),
            "rot": ("Rot", 56), "float": ("Float", 64),
            "entry": ("Entry", 110), "stop": ("Stop", 70), "tp1": ("TP1", 70),
            "rr": ("R:R", 50), "score": ("Score", 60),
        }
        self.cands = ttk.Treeview(cands_inner, columns=cols, show="headings",
                                   style="Cands.Treeview", height=12, selectmode="browse")
        for c in cols:
            label, w = headings[c]
            self.cands.heading(c, text=label, anchor="w")
            anchor = "w" if c in ("sym", "setup", "side", "conv") else "e"
            self.cands.column(c, width=w, anchor=anchor, stretch=(c == "setup"))
        self.cands.tag_configure("long", background=PANEL)
        self.cands.tag_configure("short", background=PANEL)
        self.cands.tag_configure("toppick", background="#2a2410", foreground=GOLD)
        self.cands.tag_configure("gain", foreground=GREEN)
        self.cands.tag_configure("loss", foreground=RED)
        self.cands.pack(fill="both", expand=True)

        # Movers card
        movers_card = self._make_card(self.body, "🌙 Overnight Movers (universe-wide)")
        self.movers_inner = tk.Frame(movers_card, bg=PANEL)
        self.movers_inner.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        # Backtest card
        bt_card = self._make_card(self.body, "📈 Latest Backtest")
        self.bt_inner = tk.Frame(bt_card, bg=PANEL)
        self.bt_inner.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    def _make_card(self, parent: tk.Widget, title: str,
                    border_color: str = BORDER) -> tk.Frame:
        wrapper = tk.Frame(parent, bg=border_color, padx=1, pady=1)
        wrapper.pack(fill="x", padx=12, pady=8)
        card = tk.Frame(wrapper, bg=PANEL)
        card.pack(fill="both", expand=True)
        ttk.Label(card, text=title.upper(), style="CardTitle.TLabel"
                  ).pack(anchor="w", padx=14, pady=(10, 8))
        return card

    # --- data refresh -----------------------------------------------------

    def _refresh(self) -> None:
        try:
            state = read_state()
            self._render_header(state)
            candidates = state.get("candidates") or []
            top = next((c for c in candidates if c.get("is_top_pick")), candidates[0] if candidates else None)
            self._render_top_pick(top)
            self._render_candidates(candidates)
            self._render_movers(state.get("movers") or [])
            self._render_backtest(state.get("latest_backtest"))
        except Exception as e:
            self.updated_label.configure(text=f"refresh err: {e}", foreground=RED)
        finally:
            self.root.after(self.REFRESH_MS, self._refresh)

    def _render_header(self, state: dict) -> None:
        ws = state.get("window_status", "")
        if "IN" in ws:
            self.window_pill.configure(text="🟢 IN-WINDOW", foreground=GREEN)
        elif "OFF" in ws:
            self.window_pill.configure(text="🔘 OFF-WINDOW", foreground=MUTED)
        else:
            self.window_pill.configure(text="—", foreground=MUTED)

        self.window_meta.configure(text=state.get("trading_window", ""))
        conv = (state.get("discord_min_conviction") or "high").upper()
        self.conv_pill.configure(text=f"Min {conv}", foreground=GOLD)
        self.updated_label.configure(
            text=f"Updated {_rel_time(state.get('updated_at'))}",
            foreground=MUTED,
        )

    def _render_top_pick(self, c: dict | None) -> None:
        for w in self.top_card_inner.winfo_children():
            w.destroy()
        if not c:
            ttk.Label(self.top_card_inner, text="No qualifying candidates yet.",
                      style="Muted.TLabel").pack(anchor="w", pady=10)
            return

        side_color = GREEN if c["side"] == "long" else RED

        head = tk.Frame(self.top_card_inner, bg=PANEL)
        head.pack(fill="x", pady=(0, 10))
        tk.Label(head, text=f"${c['symbol']}", bg=PANEL, fg=TEXT,
                 font=self.font_big_sym).pack(side="left")
        tk.Label(head, text=f"  {c['side'].upper()}", bg=PANEL, fg=side_color,
                 font=self.font_h1).pack(side="left")
        if c.get("setup"):
            tk.Label(head, text=f"  · {c['setup']}", bg=PANEL, fg=MUTED,
                     font=self.font_h2).pack(side="left")
        tk.Label(head, text=f"{c['score']:.1f}", bg=PANEL, fg=GOLD,
                 font=self.font_score).pack(side="right")

        # Stat grid
        stats = [
            ("Price", _fmt_money(c.get("price"))),
            ("Gap", _fmt_pct(c.get("gap_pct")), GREEN if c.get("gap_pct", 0) >= 0 else RED),
            ("RVol", f"{c.get('rvol', 0):.1f}x"),
            ("PM Vol", _fmt_num(c.get("premarket_volume"))),
            ("Float", _fmt_float(c.get("float_shares"))),
            ("Rotation", f"{c.get('rotation', 0):.2f}x"),
        ]
        srow = tk.Frame(self.top_card_inner, bg=PANEL)
        srow.pack(fill="x", pady=(0, 12))
        for i, item in enumerate(stats):
            label = item[0]
            val = item[1]
            color = item[2] if len(item) > 2 else TEXT
            cell = tk.Frame(srow, bg=PANEL2, padx=10, pady=8, bd=1, relief="flat",
                            highlightbackground=BORDER, highlightthickness=1)
            cell.grid(row=0, column=i, sticky="ew", padx=4)
            srow.grid_columnconfigure(i, weight=1, uniform="stat")
            tk.Label(cell, text=label.upper(), bg=PANEL2, fg=MUTED,
                     font=self.font_label).pack(anchor="w")
            tk.Label(cell, text=val, bg=PANEL2, fg=color,
                     font=self.font_h2).pack(anchor="w")

        # Trade plan
        lv = c.get("levels")
        if lv:
            plan = tk.Frame(self.top_card_inner, bg=PANEL2, padx=12, pady=10,
                            highlightbackground=BORDER, highlightthickness=1)
            plan.pack(fill="x", pady=(0, 10))
            tk.Label(plan, text="TRADE PLAN", bg=PANEL2, fg=MUTED,
                     font=self.font_label).pack(anchor="w")
            cells = [
                ("Entry", f"{_fmt_money(lv['entry_low'])} – {_fmt_money(lv['entry_high'])}", GREEN),
                ("Stop", _fmt_money(lv["stop"]), RED),
                (f"TP1 (R:R {lv['rr_target_1']:.2f})", _fmt_money(lv["target_1"]), BLUE),
                (f"TP2 (R:R {lv['rr_target_2']:.2f})", _fmt_money(lv["target_2"]), BLUE),
            ]
            grid = tk.Frame(plan, bg=PANEL2)
            grid.pack(fill="x", pady=(6, 0))
            for i, (label, val, color) in enumerate(cells):
                cell = tk.Frame(grid, bg=PANEL2)
                cell.grid(row=0, column=i, sticky="ew", padx=10)
                grid.grid_columnconfigure(i, weight=1, uniform="plan")
                tk.Label(cell, text=label, bg=PANEL2, fg=MUTED,
                         font=self.font_label).pack(anchor="w")
                tk.Label(cell, text=val, bg=PANEL2, fg=color,
                         font=self.font_mono).pack(anchor="w")

        # Conviction reasons
        reasons = c.get("conviction_reasons") or []
        if reasons:
            rframe = tk.Frame(self.top_card_inner, bg=PANEL)
            rframe.pack(fill="x", pady=(0, 8))
            tk.Label(rframe, text=f"WHY {c.get('conviction','low').upper()}",
                     bg=PANEL, fg=MUTED, font=self.font_label).pack(anchor="w")
            for r in reasons[:6]:
                tk.Label(rframe, text=f"  • {r}", bg=PANEL, fg=TEXT,
                         font=self.font_body).pack(anchor="w")

        # Catalyst
        cat = c.get("catalyst")
        if cat:
            cframe = tk.Frame(self.top_card_inner, bg=PANEL2,
                              highlightbackground=ORANGE if c.get("has_dilution_risk") else BLUE,
                              highlightthickness=2, padx=10, pady=8)
            cframe.pack(fill="x", pady=(0, 8))
            tags = " · ".join(cat.get("tags") or [])
            if tags:
                tk.Label(cframe, text=tags, bg=PANEL2, fg=BLUE,
                         font=self.font_label).pack(anchor="w")
            link = tk.Label(cframe, text=f"📰 {cat.get('headline','')}",
                            bg=PANEL2, fg=TEXT, font=self.font_body,
                            cursor="hand2", wraplength=1200, justify="left")
            link.pack(anchor="w")
            url = cat.get("url")
            if url:
                link.bind("<Button-1>", lambda _e, u=url: webbrowser.open(u))

        # Filing
        fil = c.get("filing")
        if fil:
            color = ORANGE if fil.get("is_dilutive") else PURPLE
            ff = tk.Frame(self.top_card_inner, bg=PANEL2,
                          highlightbackground=color, highlightthickness=2, padx=10, pady=8)
            ff.pack(fill="x", pady=(0, 8))
            tk.Label(ff, text=f"📄 {fil['form']}",
                     bg=PANEL2, fg=color, font=self.font_label).pack(anchor="w")
            link = tk.Label(ff, text=fil.get("title", ""),
                            bg=PANEL2, fg=TEXT, font=self.font_body,
                            cursor="hand2", wraplength=1200, justify="left")
            link.pack(anchor="w")
            url = fil.get("link")
            if url:
                link.bind("<Button-1>", lambda _e, u=url: webbrowser.open(u))

        # Levels block
        if lv:
            lframe = tk.Frame(self.top_card_inner, bg=PANEL2, padx=10, pady=8)
            lframe.pack(fill="x")
            text = (
                f"PMH {_fmt_money(lv['premarket_high'])} · PML {_fmt_money(lv['premarket_low'])} · VWAP {_fmt_money(lv['vwap'])}\n"
                f"PDH {_fmt_money(lv['prior_day_high'])} · PDC {_fmt_money(lv['prior_day_close'])} · PDL {_fmt_money(lv['prior_day_low'])}\n"
                f"R2 {_fmt_money(lv['r2'])} · R1 {_fmt_money(lv['r1'])} · Pivot {_fmt_money(lv['pivot'])} · S1 {_fmt_money(lv['s1'])} · S2 {_fmt_money(lv['s2'])}"
            )
            tk.Label(lframe, text=text, bg=PANEL2, fg=TEXT,
                     font=self.font_mono, justify="left").pack(anchor="w")

    def _render_candidates(self, rows: list[dict]) -> None:
        for iid in self.cands.get_children():
            self.cands.delete(iid)
        for i, c in enumerate(rows[:12], start=1):
            lv = c.get("levels") or {}
            entry = (
                f"{_fmt_money(lv['entry_low'])}–{_fmt_money(lv['entry_high']).replace('$','')}"
                if lv else "—"
            )
            stop = _fmt_money(lv.get("stop")) if lv else "—"
            tp1 = _fmt_money(lv.get("target_1")) if lv else "—"
            rr = f"{lv['rr_target_1']:.2f}" if lv else "—"
            sym = ("🥇 " if c.get("is_top_pick") else "") + f"${c['symbol']}"
            side = "🟢 L" if c["side"] == "long" else "🔴 S"
            conv = c.get("conviction", "low").upper()
            tags = ("toppick",) if c.get("is_top_pick") else (c["side"],)
            self.cands.insert(
                "", "end",
                values=(
                    i, sym, side, conv, c.get("setup", ""),
                    _fmt_money(c.get("price")),
                    _fmt_pct(c.get("gap_pct")),
                    f"{c.get('rvol', 0):.1f}x",
                    f"{c.get('rotation', 0):.2f}x",
                    _fmt_float(c.get("float_shares")),
                    entry, stop, tp1, rr,
                    f"{c.get('score', 0):.1f}",
                ),
                tags=tags,
            )

    def _render_movers(self, movers: list[dict]) -> None:
        for w in self.movers_inner.winfo_children():
            w.destroy()
        if not movers:
            ttk.Label(self.movers_inner, text="No overnight movers detected yet.",
                      style="Muted.TLabel").pack(anchor="w", pady=8)
            return
        for i, m in enumerate(movers[:2], start=1):
            move_color = GREEN if m["gap_pct"] >= 0 else RED
            arrow = "↗" if m["gap_pct"] >= 0 else "↘"
            border = ORANGE if m.get("has_dilution_risk") else BORDER
            box = tk.Frame(self.movers_inner, bg=PANEL2, padx=12, pady=10,
                           highlightbackground=border, highlightthickness=1)
            box.pack(fill="x", pady=4)

            head = tk.Frame(box, bg=PANEL2)
            head.pack(fill="x")
            tk.Label(head, text=f"#{i} ${m['symbol']}", bg=PANEL2, fg=TEXT,
                     font=self.font_h2).pack(side="left")
            tk.Label(head, text=f"  {arrow} {_fmt_pct(m['gap_pct'])}",
                     bg=PANEL2, fg=move_color, font=self.font_h2).pack(side="left")
            tk.Label(head, text=f"  {_fmt_money(m['price'])} · prev close {_fmt_money(m.get('prev_close'))}",
                     bg=PANEL2, fg=MUTED, font=self.font_body).pack(side="left")
            if m.get("has_dilution_risk"):
                tk.Label(head, text="⚠️ DILUTION", bg=PANEL2, fg=ORANGE,
                         font=self.font_label).pack(side="right")

            lv = m.get("levels")
            if lv:
                levels = (
                    f"PDH {_fmt_money(lv['prior_day_high'])} · PDC {_fmt_money(lv['prior_day_close'])} · "
                    f"PDL {_fmt_money(lv['prior_day_low'])} · VWAP {_fmt_money(lv['vwap'])} · "
                    f"R1 {_fmt_money(lv['r1'])} · S1 {_fmt_money(lv['s1'])}"
                )
                tk.Label(box, text=levels, bg=PANEL2, fg=MUTED,
                         font=self.font_mono, justify="left").pack(anchor="w", pady=(6, 0))

            cat = m.get("catalyst")
            if cat:
                row = tk.Frame(box, bg=PANEL2)
                row.pack(fill="x", pady=(4, 0))
                tk.Label(row, text="📰", bg=PANEL2).pack(side="left")
                link = tk.Label(row, text=cat.get("headline", ""),
                                bg=PANEL2, fg=TEXT, font=self.font_body,
                                cursor="hand2", wraplength=1200, justify="left")
                link.pack(side="left", anchor="w")
                url = cat.get("url")
                if url:
                    link.bind("<Button-1>", lambda _e, u=url: webbrowser.open(u))
            else:
                tk.Label(box, text="📰 no fresh catalyst", bg=PANEL2, fg=MUTED,
                         font=self.font_body).pack(anchor="w", pady=(4, 0))

            fil = m.get("filing")
            if fil:
                color = ORANGE if fil.get("is_dilutive") else PURPLE
                row = tk.Frame(box, bg=PANEL2)
                row.pack(fill="x", pady=(4, 0))
                tk.Label(row, text=f"📄 {fil['form']}: ", bg=PANEL2, fg=color,
                         font=self.font_label).pack(side="left")
                link = tk.Label(row, text=fil.get("title", ""),
                                bg=PANEL2, fg=TEXT, font=self.font_body,
                                cursor="hand2", wraplength=1100, justify="left")
                link.pack(side="left", anchor="w")
                url = fil.get("link")
                if url:
                    link.bind("<Button-1>", lambda _e, u=url: webbrowser.open(u))


    def _render_backtest(self, bt: dict | None) -> None:
        for w in self.bt_inner.winfo_children():
            w.destroy()
        if not bt:
            ttk.Label(
                self.bt_inner,
                text=("No backtest runs yet. "
                      "Run: python -m src.cli backtest setups.csv  (or wait for the auto Sunday run)"),
                style="Muted.TLabel",
            ).pack(anchor="w", pady=8)
            return

        # Header line: when, label, source
        head = tk.Frame(self.bt_inner, bg=PANEL)
        head.pack(fill="x", pady=(0, 8))
        run_at_rel = _rel_time(bt.get("run_at"))
        tk.Label(
            head,
            text=f"{bt.get('label','run')} · {bt.get('source','cli')} · {run_at_rel}",
            bg=PANEL, fg=MUTED, font=self.font_label,
        ).pack(anchor="w")

        # Headline stats
        srow = tk.Frame(self.bt_inner, bg=PANEL)
        srow.pack(fill="x", pady=(0, 10))
        cells = [
            ("Setups", str(bt.get("n_setups", 0))),
            ("Triggered", str(bt.get("n_triggered", 0))),
            ("Win rate", f"{bt.get('win_rate', 0):.1%}"),
            ("E[R]", f"{bt.get('expectancy_R', 0):+.2f}"),
            ("PF", f"{bt.get('profit_factor', 0):.2f}"),
            ("Avg win R", f"{bt.get('avg_win_R', 0):+.2f}"),
            ("Avg loss R", f"{bt.get('avg_loss_R', 0):+.2f}"),
        ]
        for i, (label, val) in enumerate(cells):
            color = TEXT
            if label == "E[R]":
                color = GREEN if bt.get("expectancy_R", 0) > 0 else RED
            cell = tk.Frame(
                srow, bg=PANEL2, padx=10, pady=6,
                highlightbackground=BORDER, highlightthickness=1,
            )
            cell.grid(row=0, column=i, sticky="ew", padx=4)
            srow.grid_columnconfigure(i, weight=1, uniform="bts")
            tk.Label(cell, text=label.upper(), bg=PANEL2, fg=MUTED,
                     font=self.font_label).pack(anchor="w")
            tk.Label(cell, text=val, bg=PANEL2, fg=color,
                     font=self.font_h2).pack(anchor="w")

        # By-setup-tag table
        by_tag = bt.get("by_setup_tag") or {}
        if by_tag:
            tk.Label(self.bt_inner, text="BY SETUP TAG", bg=PANEL, fg=MUTED,
                     font=self.font_label).pack(anchor="w", pady=(4, 4))
            tag_table = ttk.Treeview(
                self.bt_inner,
                columns=("tag", "n", "win", "expr"),
                show="headings",
                style="Cands.Treeview",
                height=min(6, len(by_tag)),
            )
            for col, label, w in [
                ("tag", "Setup tag", 200),
                ("n", "N", 60),
                ("win", "Win%", 80),
                ("expr", "E[R]", 80),
            ]:
                tag_table.heading(col, text=label)
                tag_table.column(col, width=w, anchor="w" if col == "tag" else "e")
            for tag, b in sorted(by_tag.items(), key=lambda kv: -kv[1].get("expectancy_R", 0)):
                tag_table.insert(
                    "", "end",
                    values=(
                        tag, b.get("n", 0),
                        f"{b.get('win_rate', 0):.1%}",
                        f"{b.get('expectancy_R', 0):+.2f}",
                    ),
                )
            tag_table.pack(fill="x")

        tk.Label(
            self.bt_inner,
            text=("More: python -m src.cli backtest-results --history --trades  "
                  "(file: data_cache/backtest_history.jsonl)"),
            bg=PANEL, fg=MUTED, font=self.font_label,
        ).pack(anchor="w", pady=(8, 0))


def launch(always_on_top: bool = True) -> None:
    root = tk.Tk()
    Dashboard(root, always_on_top=always_on_top)
    root.mainloop()
