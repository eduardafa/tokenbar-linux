"""System tray icon via the StatusNotifierItem + com.canonical.dbusmenu D-Bus protocols.

Implemented directly on Gio so it works inside a GTK4 process with no extra
packages (COSMIC, KDE, and GNOME with the AppIndicator extension all host SNI).
"""
from __future__ import annotations

import os
import sys

import cairo
from gi.repository import Gio, GLib

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
  </interface>
</node>"""

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"


def _color_for(used: float):
    if used >= 90:
        return (0.94, 0.33, 0.31)
    if used >= 70:
        return (0.98, 0.74, 0.25)
    return (1.0, 1.0, 1.0)


def _pct_text(p: float | None) -> str:
    return "–" if p is None else f"{min(999, round(p))}%"


def render_icon(size: int, top: float | None, bottom: float | None, alert: bool,
                wide: bool = True) -> tuple:
    """Panel icon. Returns (w, h, ARGB bytes).

    wide:   "18%  4%" — each number with a thin usage bar under it (session, weekly).
    square: big session number with a usage bar under it.
    """
    h = size
    w = size * 3 if wide else size
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    s = h / 24.0

    def segment(x, width, pct, font_px, percent_sign=True):
        text = _pct_text(pct) if percent_sign else ("–" if pct is None else str(round(pct)))
        cr.set_font_size(font_px)
        ext = cr.text_extents(text)
        if ext.width > width:  # shrink to fit
            cr.set_font_size(font_px * width / ext.width)
            ext = cr.text_extents(text)
        cr.set_source_rgb(*(_color_for(pct) if pct is not None else (1, 1, 1)))
        cr.move_to(x + (width - ext.width) / 2 - ext.x_bearing, 15.5 * s)
        cr.show_text(text)
        # usage bar
        by, bh = 18.5 * s, 3.5 * s
        cr.set_source_rgba(1, 1, 1, 0.35)
        _rounded(cr, x + s, by, width - 2 * s, bh, bh / 2)
        cr.fill()
        if pct:
            cr.set_source_rgb(*_color_for(pct))
            _rounded(cr, x + s, by, max(bh, (width - 2 * s) * min(100, pct) / 100), bh, bh / 2)
            cr.fill()

    if wide:
        gap = 4 * s
        seg = (w - gap) / 2
        segment(0, seg, top, 15 * s)
        segment(seg + gap, seg, bottom, 15 * s)
    else:
        segment(0, w, top, 19 * s, percent_sign=False)
    if alert:  # status-page incident dot
        cr.set_source_rgb(0.94, 0.33, 0.31)
        cr.arc(w - 3 * s, 3 * s, 3 * s, 0, 6.2832)
        cr.fill()
    surf.flush()
    data = bytes(surf.get_data())
    if sys.byteorder == "little":  # cairo BGRA (native) -> SNI ARGB (network order)
        out = bytearray(len(data))
        out[0::4], out[1::4], out[2::4], out[3::4] = data[3::4], data[2::4], data[1::4], data[0::4]
        data = bytes(out)
    return (w, h, data)


def _later(fn):
    """Run fn once on the main loop (idle_add repeats if the callback returns truthy)."""
    GLib.idle_add(lambda: (fn(), False)[1])


def _rounded(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


class TrayIcon:
    """menu_items: list of dicts {id?, label, enabled?, callback?} or {"separator": True}."""

    def __init__(self, app_id: str, title: str, on_activate):
        self.app_id = app_id
        self.title = title
        self.on_activate = on_activate
        self.tooltip = (title, "")
        self.wide = False  # COSMIC squeezes tray icons into a square
        self.pixmaps = self._render(None, None, False)
        self.items: list[dict] = []
        self.callbacks: dict[int, callable] = {}
        self.revision = 1
        self.bus = None
        self.bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._reg = []
        self._owner_id = 0
        self._watch_id = 0

    # ---------- lifecycle ----------
    def start(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._reg.append(self.bus.register_object(ITEM_PATH, sni, self._sni_call, self._sni_get, None))
        self._reg.append(self.bus.register_object(MENU_PATH, menu, self._menu_call, self._menu_get, None))
        self._owner_id = Gio.bus_own_name_on_connection(
            self.bus, self.bus_name, Gio.BusNameOwnerFlags.NONE, lambda *a: self._register(), None)
        # Re-register whenever the panel (watcher) restarts.
        self._watch_id = Gio.bus_watch_name_on_connection(
            self.bus, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE,
            lambda *a: self._register(), None)

    def _register(self):
        self.bus.call(
            "org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
            "RegisterStatusNotifierItem", GLib.Variant("(s)", (self.bus_name,)),
            None, Gio.DBusCallFlags.NONE, -1, None, None)

    def stop(self):
        for r in self._reg:
            self.bus.unregister_object(r)
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)

    # ---------- public updates ----------
    def set_meter(self, top, bottom, alert=False):
        self.pixmaps = self._render(top, bottom, alert)
        self._emit(ITEM_PATH, "org.kde.StatusNotifierItem", "NewIcon")

    def _render(self, top, bottom, alert):
        return [render_icon(sz, top, bottom, alert, self.wide) for sz in (24, 48)]

    def set_tooltip(self, title: str, body: str):
        self.tooltip = (title, body)
        self._emit(ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip")

    def set_menu(self, items: list[dict]):
        self.items = []
        self.callbacks = {}
        for i, it in enumerate(items, start=1):
            it = dict(it, id=i)
            if it.get("callback"):
                self.callbacks[i] = it["callback"]
            self.items.append(it)
        self.revision += 1
        self._emit(MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                   GLib.Variant("(ui)", (self.revision, 0)))

    def _emit(self, path, iface, name, params=None):
        if self.bus:
            self.bus.emit_signal(None, path, iface, name, params)

    # ---------- StatusNotifierItem ----------
    def _sni_get(self, conn, sender, path, iface, prop):
        v = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", self.app_id),
            "Title": GLib.Variant("s", self.title),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "IconPixmap": GLib.Variant("a(iiay)", self.pixmaps),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], self.tooltip[0], self.tooltip[1])),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return v.get(prop)

    def _sni_call(self, conn, sender, path, iface, method, params, invocation):
        if method in ("Activate", "SecondaryActivate"):
            _later(self.on_activate)
        invocation.return_value(None)

    # ---------- dbusmenu ----------
    def _props(self, it: dict) -> dict:
        if it.get("separator"):
            return {"type": GLib.Variant("s", "separator")}
        p = {"label": GLib.Variant("s", it["label"].replace("_", "__")),
             "enabled": GLib.Variant("b", bool(it.get("enabled", True)))}
        if it.get("icon"):
            p["icon-name"] = GLib.Variant("s", it["icon"])
        return p

    def _node(self, id_, props, children):
        return GLib.Variant("(ia{sv}av)", (id_, props, children))

    def _layout(self):
        children = [GLib.Variant("v", self._node(it["id"], self._props(it), [])) for it in self.items]
        return self._node(0, {"children-display": GLib.Variant("s", "submenu")}, children)

    def _menu_get(self, conn, sender, path, iface, prop):
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }.get(prop)

    def _menu_call(self, conn, sender, path, iface, method, params, invocation):
        if method == "GetLayout":
            layout = self._layout()
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("u", self.revision), layout))
        elif method == "GetGroupProperties":
            ids, _names = params.unpack()
            by_id = {it["id"]: it for it in self.items}
            out = []
            for i in ids:
                if i == 0:
                    out.append((0, {"children-display": GLib.Variant("s", "submenu")}))
                elif i in by_id:
                    out.append((i, self._props(by_id[i])))
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
        elif method == "GetProperty":
            i, name = params.unpack()
            it = next((x for x in self.items if x["id"] == i), None)
            val = self._props(it).get(name) if it else None
            invocation.return_value(GLib.Variant("(v)", (val or GLib.Variant("s", ""),)))
        elif method == "Event":
            i, event, _data, _ts = params.unpack()
            if event == "clicked" and i in self.callbacks:
                _later(self.callbacks[i])
            invocation.return_value(None)
        elif method == "EventGroup":
            events = params.unpack()[0]
            for i, event, _d, _t in events:
                if event == "clicked" and i in self.callbacks:
                    _later(self.callbacks[i])
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)
