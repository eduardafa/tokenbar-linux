"""GTK4/libadwaita app: tray icon + dashboard window + preferences."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, __version__, config, notify, schedule  # noqa: E402
from .models import ProviderSnapshot, fmt_duration, now  # noqa: E402
from .providers import fetch_all  # noqa: E402
from .tray import TrayIcon  # noqa: E402
from .widgets import ProviderCard  # noqa: E402

AUTOSTART = Path(GLib.get_user_config_dir()) / "autostart" / "tokenbar.desktop"


def _short_reset(w) -> str:
    if not w.resets_at:
        return ""
    return " · resets in " + fmt_duration((w.resets_at - now()).total_seconds())


def _window_height(wanted: int = 1200, margin: int = 120) -> int:
    """Tall enough to show a whole provider card without scrolling, capped to the screen."""
    try:
        from gi.repository import Gdk
        monitors = Gdk.Display.get_default().get_monitors()
        heights = [monitors.get_item(i).get_geometry().height for i in range(monitors.get_n_items())]
        if heights:
            return max(640, min(wanted, min(heights) - margin))
    except Exception:
        pass
    return 900


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: "TokenBarApp"):
        super().__init__(application=app, title=APP_NAME, default_width=440, default_height=_window_height())
        self.app = app
        self.cards: dict[str, ProviderCard] = {}

        header = Adw.HeaderBar()
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh now")
        self.refresh_btn.connect("clicked", lambda *_: app.refresh("force"))
        self.spinner = Gtk.Spinner()
        header.pack_start(self.refresh_btn)
        header.pack_start(self.spinner)

        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu.append("About TokenBar", "app.about")
        menu.append("Quit", "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.set_margin_top(12)
        self.box.set_margin_bottom(18)
        self.box.set_margin_start(12)
        self.box.set_margin_end(12)
        self.empty = Adw.StatusPage(
            icon_name="dialog-information-symbolic", title="No providers found",
            description="Log in to Claude Code (`claude`) or Codex (`codex`) and refresh.")
        self.empty.set_visible(False)
        self.box.append(self.empty)

        clamp = Adw.Clamp(maximum_size=560, child=self.box)
        scroller = Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_vexpand(True)
        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(header)
        self.set_content(view)
        self.connect("close-request", self._on_close)

    def _on_close(self, *_):
        if self.app.tray is None:
            return False  # no tray: closing the window quits
        self.set_visible(False)  # keep running in the tray
        return True

    def set_loading(self, loading: bool):
        self.spinner.set_spinning(loading)
        self.spinner.set_visible(loading)
        self.refresh_btn.set_sensitive(not loading)

    def update(self, snaps: list[ProviderSnapshot]):
        shown = [s for s in snaps if s.available]
        for s in shown:
            card = self.cards.get(s.id)
            if card is None:
                card = self.cards[s.id] = ProviderCard()
                self.box.append(card)
            card.update(s)
        ids = {s.id for s in shown}
        for pid, card in self.cards.items():
            card.set_visible(pid in ids)
        self.empty.set_visible(not shown)


class TokenBarApp(Adw.Application):
    def __init__(self, start_hidden: bool, use_tray: bool = True, open_prefs: bool = False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.cfg = config.load()
        self.start_hidden = start_hidden
        self.use_tray = use_tray
        self.open_prefs = open_prefs
        self.snaps: list[ProviderSnapshot] = []
        self.window: MainWindow | None = None
        self.tray: TrayIcon | None = None
        self._timer = 0
        self._loading = False
        self._last_refresh: float | None = None  # last time we asked the API (not cache-only)
        self._last_open: float | None = None     # last time the window or tray menu was opened

    # ---------- lifecycle ----------
    def do_startup(self):
        Adw.Application.do_startup(self)
        if self.use_tray:
            self.hold()  # stay alive with no window
        for name, cb in (("preferences", self.show_preferences), ("about", self.show_about),
                         ("quit", self.quit_app), ("refresh", lambda *_: self.refresh("force"))):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, cb=cb: cb())
            self.add_action(act)
        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        self.set_accels_for_action("app.refresh", ["<Ctrl>r", "F5"])

        if self.use_tray:
            self.tray = TrayIcon(APP_ID, APP_NAME, self.toggle_window, on_menu_open=self._opened)
            try:
                self.tray.start()
            except Exception as e:  # no session bus / tray host: dashboard still works
                print(f"tray unavailable: {e}", file=sys.stderr)
                self.tray = None
        self._update_tray()
        self._schedule()
        self.refresh("offline")  # cached data only; the API is called when you open the window

    def do_activate(self):
        if self.start_hidden and self.tray:
            self.start_hidden = False
            return
        self.show_window()
        if self.open_prefs:
            self.open_prefs = False
            self.show_preferences()

    def quit_app(self):
        if self.tray:
            self.tray.stop()
            self.release()
        self.quit()

    # ---------- window ----------
    def show_window(self):
        if self.window is None:
            self.window = MainWindow(self)
            self.window.update(self.snaps)
            self.window.set_loading(self._loading)
        self.window.present()
        self._opened()

    def _opened(self):
        """Window or tray menu opened: fetch now (2-minute cache) and refresh faster for a while."""
        self._last_open = time.time()
        self.refresh()

    def toggle_window(self):
        if self.window and self.window.get_visible() and self.window.is_active():
            self.window.set_visible(False)
        else:
            self.show_window()

    # ---------- refresh ----------
    def _schedule(self):
        """Check once a minute whether an adaptive background refresh is due (see schedule.py)."""
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add_seconds(60, lambda: (self._tick(), True)[1])

    def _tick(self):
        if self.cfg.get("background_refresh", True) and schedule.due(self._last_refresh, self._last_open):
            self.refresh()
        else:
            self.refresh("offline")  # local logs only: keeps today's cost current

    def refresh(self, mode: str = "normal"):
        """mode: "offline" (cache only), "normal" (2-minute cache), "force" (manual refresh)."""
        if self._loading:
            return
        self._loading = True
        if self.window:
            self.window.set_loading(True)
        cfg = dict(self.cfg)

        def work():
            snaps = fetch_all(cfg, mode)
            if mode != "offline":
                notify.evaluate(snaps, cfg)
            GLib.idle_add(self._apply, snaps, mode)

        threading.Thread(target=work, daemon=True).start()

    def _apply(self, snaps, mode="normal"):
        self._loading = False
        if mode != "offline":
            self._last_refresh = time.time()
        self.snaps = snaps
        if self.window:
            self.window.set_loading(False)
            self.window.update(snaps)
        self._update_tray()
        return False

    # ---------- tray ----------
    def _update_tray(self):
        if not self.tray:
            return
        shown = [s for s in self.snaps if s.available]
        items, tip = [], []
        for s in shown:
            items.append({"label": s.name + (f" · {s.plan}" if s.plan else ""), "enabled": False})
            for w in s.windows:
                line = f"{w.label}: {w.used_percent:.0f}% used{_short_reset(w)}"
                items.append({"label": "    " + line, "enabled": False})
                tip.append(f"{s.name} {line}")
            if s.credits:
                items.append({"label": "    " + s.credits, "enabled": False})
            if s.cost and s.cost.month_tokens:
                items.append({"label": f"    Today ${s.cost.today_cost:,.2f} · 30 days ${s.cost.month_cost:,.2f}",
                              "enabled": False})
            if s.status and s.status.indicator not in ("none", ""):
                items.append({"label": f"    ⚠ {s.status.description}", "enabled": False})
            if s.error:
                items.append({"label": f"    ! {s.error[:70]}", "enabled": False})
            items.append({"separator": True})
        if not shown:
            items += [{"label": "Loading…" if self._loading or not self.snaps else "No providers found",
                       "enabled": False}, {"separator": True}]
        items += [
            {"label": "Open Dashboard", "callback": self.show_window},
            {"label": "Refresh Now", "callback": lambda: self.refresh("force")},
            {"label": "Preferences…", "callback": self.show_preferences},
            {"separator": True},
            {"label": "Quit", "callback": self.quit_app},
        ]
        self.tray.set_menu(items)
        self.tray.set_tooltip(APP_NAME, "\n".join(tip) or "No data yet")

    # ---------- dialogs ----------
    def _parent(self):
        if self.window is None or not self.window.get_visible():
            self.show_window()
        return self.window

    def show_about(self):
        Adw.AboutDialog(
            application_name=APP_NAME, version=__version__, application_icon="io.github.eduardafranca.TokenBar",
            developer_name="Eduarda França", license_type=Gtk.License.MIT_X11,
            comments="Usage limits, reset countdowns and spend for AI coding plans, in your Pop!_OS panel.",
        ).present(self._parent())

    def show_preferences(self):
        dlg = Adw.PreferencesDialog(title="Preferences")
        page = Adw.PreferencesPage()
        dlg.add(page)

        general = Adw.PreferencesGroup(title="General")
        page.add(general)
        bg = Adw.SwitchRow(title="Background refresh",
                           subtitle="Adaptive: every 2 min after you look, up to 30 min when idle",
                           active=self.cfg.get("background_refresh", True))
        bg.connect("notify::active", lambda r, _p: self._set("background_refresh", r.get_active()))
        general.add(bg)

        login = Adw.SwitchRow(title="Start at login", active=AUTOSTART.exists())
        login.connect("notify::active", lambda r, _p: self._set_autostart(r.get_active()))
        general.add(login)

        provs = Adw.PreferencesGroup(title="Providers")
        page.add(provs)
        for pid, name, sub in (("claude", "Claude", "Uses the Claude Code login"),
                               ("codex", "Codex", "Uses the Codex CLI login")):
            row = Adw.SwitchRow(title=name, subtitle=sub, active=self.cfg["providers"].get(pid, True))
            row.connect("notify::active", lambda r, _p, pid=pid: self._set_provider(pid, r.get_active()))
            provs.add(row)

        extras = Adw.PreferencesGroup(title="Extras")
        page.add(extras)
        for key, title, sub in (("show_cost", "Cost estimate", "Scan local Claude Code logs for token spend"),
                                ("check_status", "Service status", "Show incidents from provider status pages")):
            row = Adw.SwitchRow(title=title, subtitle=sub, active=self.cfg.get(key, True))
            row.connect("notify::active", lambda r, _p, key=key: self._set(key, r.get_active()))
            extras.add(row)

        alerts = Adw.PreferencesGroup(title="Notifications",
                                      description="Need background refresh to arrive while you're not looking")
        page.add(alerts)
        kinds = (("thresholds", "Threshold warnings", "When session or weekly usage reaches 80% and 95%"),
                 ("pace", "Pace warnings", "When the current pace would run out before the limit resets"),
                 ("depleted", "Session depleted & restored", "When the session hits 0% left, and when it's back"),
                 ("reset", "Limit resets", "When a session or weekly limit resets"),
                 ("login", "Sign-in needed", "When a provider login expires"))
        current = {**notify.DEFAULTS, **(self.cfg.get("notifications") or {})}
        for key, title, sub in kinds:
            row = Adw.SwitchRow(title=title, subtitle=sub, active=current[key])
            row.connect("notify::active", lambda r, _p, key=key: self._set_notification(key, r.get_active()))
            alerts.add(row)

        dlg.present(self._parent())

    def _set(self, key, value):
        self.cfg[key] = value
        config.save(self.cfg)
        if key != "background_refresh":
            self.refresh()

    def _set_notification(self, kind, on):
        self.cfg["notifications"] = {**notify.DEFAULTS, **(self.cfg.get("notifications") or {}), kind: on}
        config.save(self.cfg)

    def _set_provider(self, pid, on):
        self.cfg["providers"][pid] = on
        config.save(self.cfg)
        self.refresh()

    def _set_autostart(self, on: bool):
        if on:
            AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART.write_text(
                "[Desktop Entry]\nType=Application\nName=TokenBar\n"
                f"Exec={_launcher()} --hidden\nIcon=io.github.eduardafranca.TokenBar\n"
                "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n")
        elif AUTOSTART.exists():
            AUTOSTART.unlink()


def _launcher() -> str:
    local = Path.home() / ".local" / "bin" / "tokenbar"
    return str(local) if local.exists() else f"{sys.executable} -m tokenbar"


def run() -> int:
    hidden = "--hidden" in sys.argv
    no_tray = "--no-tray" in sys.argv
    prefs = "--preferences" in sys.argv
    app = TokenBarApp(start_hidden=hidden and not no_tray and not prefs, use_tray=not no_tray, open_prefs=prefs)
    if prefs:
        # If TokenBar is already running, a second launch would only raise its window.
        # Ask the running instance to open Preferences instead.
        app.register(None)
        if app.get_is_remote():
            app.activate_action("preferences", None)
            return 0
    return app.run(sys.argv[:1])
