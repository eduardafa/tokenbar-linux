"""Custom GTK4 widgets: usage meter with pace marker, daily cost chart, provider card."""
from __future__ import annotations

import webbrowser
from datetime import datetime

from gi.repository import Adw, Gtk

from .models import ProviderSnapshot, UsageWindow, fmt_duration, fmt_pace, fmt_reset, fmt_tokens, now


def _fg(widget):
    c = widget.get_color()
    return c.red, c.green, c.blue


def _severity_rgb(used: float):
    if used >= 90:
        return (0.88, 0.27, 0.25)
    if used >= 70:
        return (0.90, 0.62, 0.10)
    return (0.21, 0.52, 0.89)


def _rounded(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


class Meter(Gtk.DrawingArea):
    """Horizontal bar of % used, with a tick where an even burn rate would be."""

    def __init__(self):
        super().__init__()
        self.used = 0.0
        self.expected = None
        self.color = None  # brand colour; None = severity colour
        self.set_content_height(10)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)

    def update(self, used: float, expected: float | None):
        self.used, self.expected = used, expected
        self.queue_draw()

    def _draw(self, area, cr, w, h):
        r, g, b = _fg(self)
        cr.set_source_rgba(r, g, b, 0.12)
        _rounded(cr, 0, 0, w, h, h / 2)
        cr.fill()
        if self.used > 0:
            cr.set_source_rgb(*(self.color or _severity_rgb(self.used)))
            _rounded(cr, 0, 0, max(h, w * min(100, self.used) / 100), h, h / 2)
            cr.fill()
        if self.expected is not None:
            x = w * min(100, self.expected) / 100
            cr.set_source_rgba(r, g, b, 0.75)
            cr.rectangle(x - 1, -1, 2, h + 2)
            cr.fill()


BRAND = {"claude": (0.851, 0.467, 0.341), "codex": (0.063, 0.639, 0.498)}
PEAK_CAP = (1.0, 0.80, 0.0)


class CostChart(Gtk.DrawingArea):
    """CodexBar-style history: one bar per day in the provider colour, peak day capped
    in yellow. Hovering selects a day; `on_select(index)` is called."""

    def __init__(self, on_select=None):
        super().__init__()
        self.daily: list = []
        self.metric = "cost"
        self.selected: int | None = None
        self.color = BRAND["claude"]
        self.on_select = on_select
        self.set_content_height(130)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

    def values(self):
        return [d["cost"] if self.metric == "cost" else d["tokens"] for d in self.daily]

    def update(self, daily: list, color=None):
        self.daily = daily
        self.color = color or self.color
        if self.selected is None or self.selected >= len(daily):
            nonzero = [i for i, d in enumerate(daily) if d["cost"] or d["tokens"]]
            self.selected = nonzero[-1] if nonzero else None
        self.queue_draw()
        if self.on_select:
            self.on_select(self.selected)

    def set_metric(self, metric):
        self.metric = metric
        self.queue_draw()

    def _on_motion(self, _c, x, _y):
        n = len(self.daily)
        if not n:
            return
        i = min(n - 1, max(0, int(x / (self.get_width() / n))))
        if i != self.selected:
            self.selected = i
            self.queue_draw()
            if self.on_select:
                self.on_select(i)

    def _draw(self, area, cr, w, h):
        vals = self.values()
        if not vals:
            return
        r, g, b = _fg(self)
        n = len(vals)
        slot = w / n
        bw = max(1.0, slot - 2)
        cr.set_source_rgba(r, g, b, 0.15)
        cr.rectangle(0, h - 1, w, 1)
        cr.fill()
        peak = max(vals)
        if peak <= 0:
            return
        peak_i = vals.index(peak)
        for i, v in enumerate(vals):
            if i == self.selected:
                cr.set_source_rgba(r, g, b, 0.08)
                cr.rectangle(i * slot, 0, slot, h - 1)
                cr.fill()
            if v <= 0:
                continue
            bh = max(2.0, (h - 2) * v / peak)
            x, y = i * slot + 1, h - 1 - bh
            alpha = 1.0 if self.selected in (None, i) else 0.55
            cr.set_source_rgba(*self.color, alpha)
            _top_rounded(cr, x, y, bw, bh, min(3, bw / 2))
            cr.fill()
            if i == peak_i:
                cr.set_source_rgb(*PEAK_CAP)
                _top_rounded(cr, x, y, bw, min(4, bh), min(3, bw / 2))
                cr.fill()


def _top_rounded(cr, x, y, w, h, r):
    r = min(r, w / 2, h)
    cr.new_sub_path()
    cr.move_to(x, y + h)
    cr.line_to(x, y + r)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.arc(x + w - r, y + r, r, 4.7124, 0)
    cr.line_to(x + w, y + h)
    cr.close_path()


def _clear(box):
    while (child := box.get_first_child()) is not None:
        box.remove(child)


def _label(text="", css=(), xalign=0.0, wrap=False):
    lbl = Gtk.Label(label=text, xalign=xalign)
    for c in css:
        lbl.add_css_class(c)
    if wrap:
        lbl.set_wrap(True)
    return lbl


class WindowRow(Gtk.Box):
    """CodexBar layout: title / bar / "x% used ...... Resets in 3h 53m" / pace line."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.title = _label(css=("heading",))
        self.meter = Meter()
        bottom = Gtk.Box(spacing=6)
        self.pct = _label(css=("numeric",))
        self.pct.set_hexpand(True)
        self.reset = _label(css=("dim-label",), xalign=1)
        bottom.append(self.pct)
        bottom.append(self.reset)
        self.pace = _label(css=("dim-label", "caption"))
        for wdg in (self.title, self.meter, bottom, self.pace):
            self.append(wdg)

    def update(self, w: UsageWindow, color=None):
        p = w.pace()
        self.title.set_text(w.label)
        self.pct.set_text(f"{w.used_percent:.0f}% used")
        self.meter.color = color
        self.meter.update(w.used_percent, None)
        self.reset.set_text(fmt_reset(w.resets_at))
        line = ""
        if p and w.key != "session":
            delta = w.used_percent - p.expected_percent
            head = "On pace" if abs(delta) < 2 else (f"Behind ({delta:.0f}%)" if delta < 0 else f"Ahead (+{delta:.0f}%)")
            tail = "Lasts to reset" if p.lasts else fmt_pace(p)
            line = f"Pace: {head} · {tail}"
        self.pace.set_text(line)
        self.pace.set_visible(bool(line))


class ProviderCard(Gtk.Box):
    """One provider: header, status banner, usage windows, credits, cost."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add_css_class("card")
        for side in ("top", "bottom", "start", "end"):
            getattr(self, f"set_margin_{side}")(0)
        self.inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.inner.set_margin_top(16)
        self.inner.set_margin_bottom(16)
        self.inner.set_margin_start(16)
        self.inner.set_margin_end(16)
        self.append(self.inner)

        head = Gtk.Box(spacing=8)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        titles.set_hexpand(True)
        self.name = _label(css=("title-3",))
        self.sub = _label(css=("dim-label", "caption"))
        self.sub.set_ellipsize(3)
        titles.append(self.name)
        titles.append(self.sub)
        self.plan = _label(css=("caption", "accent"))
        self.plan.set_valign(Gtk.Align.START)
        self.open_btn = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text="Open usage page")
        self.open_btn.add_css_class("flat")
        self.open_btn.set_valign(Gtk.Align.START)
        self.open_btn.connect("clicked", lambda *_: self.url and webbrowser.open(self.url))
        self.url = None
        head.append(titles)
        head.append(self.plan)
        head.append(self.open_btn)
        self.inner.append(head)

        self.status = _label(css=("caption", "warning"), wrap=True)
        self.inner.append(self.status)
        self.windows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.inner.append(self.windows_box)
        self.credits = _label(css=("caption",))
        self.inner.append(self.credits)
        self.error = _label(css=("caption", "error"), wrap=True)
        self.error.set_selectable(True)
        self.inner.append(self.error)

        # cost section
        self.cost_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.cost_box.append(Gtk.Separator())
        stats = Gtk.Box(spacing=12, homogeneous=True)
        self.today = self._stat(stats, "Today")
        self.month = self._stat(stats, "Last 30 days")
        self.cost_box.append(stats)
        hist_head = Gtk.Box(spacing=6)
        hist_title = _label("Daily cost", css=("heading",))
        hist_title.set_hexpand(True)
        hist_head.append(hist_title)
        toggle = Gtk.Box(css_classes=["linked"])
        self.metric_btns = {}
        for key, label in (("cost", "Cost"), ("tokens", "Tokens")):
            btn = Gtk.ToggleButton(label=label, active=key == "cost", css_classes=["flat", "caption"])
            if self.metric_btns:
                btn.set_group(next(iter(self.metric_btns.values())))
            btn.connect("toggled", lambda b, k=key: b.get_active() and self._set_metric(k))
            self.metric_btns[key] = btn
            toggle.append(btn)
        hist_head.append(toggle)
        self.cost_box.append(hist_head)

        chart_row = Gtk.Box(spacing=6)
        self.chart = CostChart(on_select=self._show_day)
        chart_row.append(self.chart)
        axis = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        axis.set_size_request(44, -1)
        self.y_top = _label(css=("dim-label", "caption"), xalign=1)
        self.y_mid = _label(css=("dim-label", "caption"), xalign=1)
        self.y_mid.set_vexpand(True)
        self.y_mid.set_valign(Gtk.Align.CENTER)
        y0 = _label("0", css=("dim-label", "caption"), xalign=1)
        for wdg in (self.y_top, self.y_mid, y0):
            axis.append(wdg)
        chart_row.append(axis)
        chart_row.set_vexpand(False)  # stop the axis label's vexpand from stretching the chart
        self.cost_box.append(chart_row)

        x_axis = Gtk.Box()
        x_axis.set_margin_end(50)
        self.x_labels = [_label(css=("dim-label", "caption"), xalign=x) for x in (0, 0.5, 1)]
        for lbl in self.x_labels:
            lbl.set_hexpand(True)
            x_axis.append(lbl)
        self.cost_box.append(x_axis)

        self.day_title = _label(css=("caption",))
        self.cost_box.append(self.day_title)
        self.day_models = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.cost_box.append(self.day_models)
        self.cost_box.append(Gtk.Separator())
        self.total = _label(css=("dim-label", "caption"))
        self.cost_box.append(self.total)
        self.models_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.cost_box.append(self.models_box)
        self._cost = None
        note = _label("API-equivalent estimate from local Claude Code logs", css=("dim-label", "caption"))
        note.set_opacity(0.7)
        self.cost_box.append(note)
        self.inner.append(self.cost_box)
        self._rows: list[WindowRow] = []

    def _stat(self, parent, title):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.append(_label(title, css=("dim-label", "caption")))
        value = _label(css=("title-2", "numeric"))
        detail = _label(css=("dim-label", "caption", "numeric"))
        box.append(value)
        box.append(detail)
        parent.append(box)
        return value, detail

    def _set_metric(self, metric):
        self.chart.set_metric(metric)
        self._update_axes()

    def _update_axes(self):
        c = self._cost
        if not c or not c.daily:
            return
        vals = self.chart.values()
        peak = max(vals) if vals else 0
        fmt = (lambda v: f"${v:,.0f}") if self.chart.metric == "cost" else (lambda v: fmt_tokens(int(v)))
        self.y_top.set_text(fmt(peak))
        self.y_mid.set_text(fmt(peak / 2))
        days = c.daily
        for lbl, d in zip(self.x_labels, (days[0], days[len(days) // 2], days[-1])):
            lbl.set_text(datetime.fromisoformat(d["date"]).strftime("%b %-d"))

    def _show_day(self, i):
        _clear(self.day_models)
        c = self._cost
        if not c or i is None:
            self.day_title.set_text("Hover a bar for details")
            return
        d = c.daily[i]
        label = datetime.fromisoformat(d["date"]).strftime("%b %-d")
        self.day_title.set_text(f"{label}: ${d['cost']:,.2f} · {fmt_tokens(d['tokens'])} tokens")
        base = self.chart.color
        for n, m in enumerate(d["models"]):
            row = Gtk.Box(spacing=8)
            swatch = Gtk.DrawingArea()
            swatch.set_size_request(4, 30)
            alpha = max(0.35, 1 - 0.2 * n)
            swatch.set_draw_func(lambda a, cr, w, h, al=alpha: (cr.set_source_rgba(*base, al), _rounded(cr, 0, 0, w, h, 2), cr.fill()))
            row.append(swatch)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            col.append(_label(m["name"], css=("caption", "heading")))
            col.append(_label(f"${m['cost']:,.2f} · {fmt_tokens(m['tokens'])} tokens", css=("dim-label", "caption")))
            col.append(_label(
                f"in {fmt_tokens(m['input'])} · out {fmt_tokens(m['output'])} · "
                f"cache read {fmt_tokens(m['cache_read'])} · write {fmt_tokens(m['cache_write'])}",
                css=("dim-label", "caption")))
            row.append(col)
            self.day_models.append(row)

    def update(self, s: ProviderSnapshot):
        self.name.set_text(s.name)
        age = (now() - s.updated_at).total_seconds() if s.updated_at else None
        updated = "" if age is None else (f"Updated {fmt_duration(age)} ago" if age >= 60 else "Updated just now")
        bits = [s.account or "", updated]
        self.sub.set_text(" · ".join(b for b in bits if b))
        self.plan.set_text(s.plan or "")
        self.plan.set_visible(bool(s.plan))
        self.url = s.dashboard_url
        self.open_btn.set_visible(bool(self.url))

        st = s.status
        incident = bool(st and st.indicator not in ("none", ""))
        self.status.set_visible(incident)
        if incident:
            self.status.set_text(f"⚠ {st.description}")

        while len(self._rows) < len(s.windows):
            row = WindowRow()
            self._rows.append(row)
            self.windows_box.append(row)
        for i, row in enumerate(self._rows):
            row.set_visible(i < len(s.windows))
            if i < len(s.windows):
                row.update(s.windows[i], BRAND.get(s.id))
        self.windows_box.set_visible(bool(s.windows))

        self.credits.set_text(s.credits or "")
        self.credits.set_visible(bool(s.credits))
        self.error.set_text(s.error or "")
        self.error.set_visible(bool(s.error))

        c = s.cost
        self.cost_box.set_visible(bool(c and c.month_tokens))
        if c and c.month_tokens:
            self.today[0].set_text(f"${c.today_cost:,.2f}")
            self.today[1].set_text(f"{fmt_tokens(c.today_tokens)} tokens")
            self.month[0].set_text(f"${c.month_cost:,.2f}")
            self.month[1].set_text(f"{fmt_tokens(c.month_tokens)} tokens")
            self._cost = c
            self.chart.update(c.daily, BRAND.get(s.id))
            self._update_axes()
            self.total.set_text(f"Est. total (Last 30 days): ${c.month_cost:,.2f}")
            _clear(self.models_box)
            for m in c.models:
                row = Gtk.Box(spacing=6)
                name = _label(m["name"], css=("caption",))
                name.set_hexpand(True)
                row.append(name)
                row.append(_label(f"${m['cost']:,.2f} · {fmt_tokens(m['tokens'])} tokens", css=("dim-label", "caption")))
                self.models_box.append(row)
