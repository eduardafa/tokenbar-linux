"""System tray icon via the StatusNotifierItem + com.canonical.dbusmenu D-Bus protocols.

Implemented directly on Gio so it works inside a GTK4 process with no extra
packages (COSMIC, KDE, and GNOME with the AppIndicator extension all host SNI).
"""
from __future__ import annotations

import os

from pathlib import Path

import gi
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


ICON_NAME = "io.github.eduardafranca.TokenBar-symbolic"


def _icon_file() -> Path | None:
    for d in (Path(__file__).resolve().parent.parent / "data" / "icons",
              Path(GLib.get_user_data_dir()) / "icons" / "hicolor" / "symbolic" / "apps"):
        if (d / f"{ICON_NAME}.svg").is_file():
            return d / f"{ICON_NAME}.svg"
    return None


def icon_pixmaps() -> list:
    """White renderings of the symbolic SVG, for tray hosts that ignore IconName."""
    path = _icon_file()
    if not path:
        return []
    try:
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        svg = path.read_text().replace("#2e3436", "#ffffff").encode()
        out = []
        for size in (22, 44):
            loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
            loader.set_size(size, size)
            loader.write(svg)
            loader.close()
            pb = loader.get_pixbuf()
            if not pb.get_has_alpha():
                pb = pb.add_alpha(False, 0, 0, 0)
            px, stride = pb.get_pixels(), pb.get_rowstride()
            argb = bytearray()
            for y in range(size):  # RGBA rows -> ARGB (network byte order)
                row = px[y * stride: y * stride + size * 4]
                for x in range(0, size * 4, 4):
                    r, g, b, a = row[x:x + 4]
                    argb += bytes((a, r, g, b))
            out.append((size, size, bytes(argb)))
        return out
    except Exception:
        return []


def _later(fn):
    """Run fn once on the main loop (idle_add repeats if the callback returns truthy)."""
    GLib.idle_add(lambda: (fn(), False)[1])


class TrayIcon:
    """menu_items: list of dicts {id?, label, enabled?, callback?} or {"separator": True}."""

    def __init__(self, app_id: str, title: str, on_activate, on_menu_open=None):
        self.app_id = app_id
        self.title = title
        self.on_activate = on_activate
        self.on_menu_open = on_menu_open
        self.tooltip = (title, "")
        self.pixmaps = icon_pixmaps()
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
            "IconName": GLib.Variant("s", ICON_NAME),
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
            if params.unpack()[0] == 0 and self.on_menu_open:  # root menu is opening
                _later(self.on_menu_open)
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)
