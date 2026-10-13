#!/usr/bin/env python3
"""SiriusXM Radio ID Checker - standalone GUI.

Extracted from the dealer CLI's subscription check
(``sxm_api.SxmClient.check`` in the Bs-SXM-Dealer / Siriusxm project).

The check POSTs a radio ID to the SiriusXM "care" registration endpoint and
returns the subscription plans on file:

    https://care.siriusxm.com/services/account/registration/non-pii
    https://care.siriusxm.ca/services/account/registration/non-pii

Both markets are queried. By default the United States endpoint is tried first
and Canada second (the CLI's default region order); choose "Canada first" to
flip that. The plan with the latest valid end date wins, and its type/status
distinguish a running trial/demo from a paid subscription or an expired one.

"Request Signal View Web" opens SiriusXM's official refresh-signal web page in
the user's own browser and puts the Radio ID on the clipboard ready to paste.
The public page is used instead of the dealer signal API because that API
requires the dealer app key/secret, and this tool deliberately embeds no
credentials and drives no browser of its own. This app never renews or changes a
subscription - renewal / trial activation is intentionally out of scope.

Recently used Radio IDs are remembered between runs and offered in a "Recent"
dropdown for one-click reuse, each with the expiry date the last check found
("Unknown" when no check has produced one). The list is a plain local file
(``%APPDATA%\SXMChecker\history.json`` on Windows); nothing is sent anywhere.

Only the Python standard library plus ``requests`` is required. ``build_exe.py``
freezes this into a single .exe so end users do not need Python installed.
"""
from __future__ import annotations

import json
import os
import queue
import re
import sys
import tempfile
import threading
import webbrowser
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import requests

CHECK_URL = "https://care.siriusxm.{region}/services/account/registration/non-pii"
REGION_NAMES = {"com": "United States", "ca": "Canada"}

# Official SiriusXM pages that send a refresh signal to a radio. These redirect
# to the current care "refresh-signal" flow, so the short entry URL stays valid.
SIGNAL_PAGES = {
    "com": "https://www.siriusxm.com/refresh",
    "ca": "https://www.siriusxm.ca/refresh",
}


def signal_url(region):
    """Return the official refresh-signal web page for a market."""
    return SIGNAL_PAGES.get(region, SIGNAL_PAGES["com"])


ICON_RELATIVE = "assets/sxm_icon.ico"


def resource_path(relative):
    """Locate a bundled resource in-source and inside the frozen executable."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def window_icon():
    """Path to the application icon, or None when it is not available."""
    path = resource_path(ICON_RELATIVE)
    return path if path.exists() else None


# The Settings button's gear, drawn in two tones so it reads on either kind of
# theme. 20px is the native size; 32px is there for scaled displays, where the
# smaller one would look soft.
def settings_icon(variant="dark", scale=1):
    """Path to the Settings gear for a theme variant, or None when missing.

    ``variant`` picks the glyph tone ("dark" on light themes, "light" on dark
    ones); ``scale`` is the display scaling factor, so a 150% screen gets the
    larger artwork instead of a blurry upscale.
    """
    size = 32 if scale and scale >= 2 else 20
    path = resource_path(f"assets/settings_gear_{variant}_{size}.png")
    return path if path.exists() else None


def _set_native_window_icons(root, icon_path):
    """Hand the .ico's native 32px/16px frames to the window via WM_SETICON.

    Tk installs icons on the window *class*, and the large frame that ends up
    there can be a stretched copy of the 16px image. Sending the correctly sized
    frames keeps the title bar and Alt+Tab crisp.
    """
    try:
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_size_t, ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]

        root.update_idletasks()
        hwnds = [int(root.winfo_id())]
        parent = user32.GetParent(hwnds[0])          # Tk id is the client window
        if parent:
            hwnds.append(int(parent))

        applied = False
        for slot, size in ((1, 32), (0, 16)):        # ICON_BIG, ICON_SMALL
            icon = user32.LoadImageW(None, str(icon_path), 1, size, size, 0x0010)
            if not icon:                             # IMAGE_ICON, LR_LOADFROMFILE
                continue
            for hwnd in hwnds:
                user32.SendMessageW(hwnd, 0x0080, slot, icon)   # WM_SETICON
            applied = True
        return applied
    except Exception:
        return False


def apply_window_icon(root, icon_path=None):
    """Give the window its icon, in both places Windows looks for one."""
    icon_path = icon_path or window_icon()
    if icon_path is None:
        return False
    try:
        root.iconbitmap(default=str(icon_path))      # class icon, inherited by dialogs
    except Exception:
        pass
    if sys.platform == "win32":
        return _set_native_window_icons(root, icon_path)
    return True


def ico_frame_sizes(icon_path):
    """Pixel sizes stored in an .ico file (0 in the header means 256)."""
    try:
        data = Path(icon_path).read_bytes()
    except OSError:
        return []
    if len(data) < 6 or data[:4] != b"\x00\x00\x01\x00":
        return []
    count = int.from_bytes(data[4:6], "little")
    sizes = []
    for index in range(count):
        entry = 6 + index * 16
        if entry + 16 > len(data):
            break
        width, height = data[entry], data[entry + 1]
        sizes.append((width or 256, height or 256))
    return sizes


def png_size(path):
    """(width, height) from a PNG's own header, or None - no image library needed.

    The frozen build has to check its artwork too, and Pillow is not bundled.
    """
    try:
        header = Path(path).read_bytes()[:24]
    except (OSError, TypeError):
        return None
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    return (int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big"))


# ---------------------------------------------------------------------------
# Recent Radio IDs (remembered between runs for one-click reuse)
# ---------------------------------------------------------------------------
# Every remembered entry keeps the Radio ID and the expiry date the last check
# found, so the list can say when a remembered radio runs out. It reads
# "Unknown" until a check produces a date.

HISTORY_RELATIVE = "history.json"
HISTORY_LIMIT = 15          # newest first; older entries fall off the end
UNKNOWN_EXPIRY = "Unknown"
RADIO_ID_MIN = 8            # a Radio ID, or a 12-digit id
VIN_LENGTH = 17             # a VIN is the longest input, so nothing needs to be wider
RECENT_WIDTH = 30           # minimum width of the Recent box, in characters (it also stretches)


def history_path():
    """Where the recent-Radio-ID list lives.

    A per-user folder keeps the list writable when running from the frozen
    .exe, whose own directory is temporary and must not be written to.
    """
    base = os.environ.get("APPDATA") or os.environ.get("XDG_DATA_HOME")
    root = Path(base) / "SXMChecker" if base else Path.home() / ".sxm_checker"
    return root / HISTORY_RELATIVE


def valid_radio_id(value):
    """True for anything shaped like a Radio ID / VIN / 12-digit id."""
    return bool(re.fullmatch(rf"[A-Z0-9]{{{RADIO_ID_MIN},{VIN_LENGTH}}}",
                             normalize_id(value or "")))


def normalize_expiry(value):
    """A stored expiry as YYYY-MM-DD, or None when it is not a real date."""
    return valid_date(value) if isinstance(value, str) else None


def _history_entry(raw):
    """One stored row -> {'id', 'endDate'}, or None when it is unusable.

    Plain strings (the first format this file used) still load, as do a couple of
    alternative key names, so an older or hand-edited file keeps working.
    """
    if isinstance(raw, str):
        radio_id, expiry = raw, None
    elif isinstance(raw, dict):
        radio_id = raw.get("id") or raw.get("radioId") or raw.get("radio_id") or ""
        expiry = raw.get("endDate") or raw.get("end") or raw.get("expiry") or raw.get("expires")
    else:
        return None
    radio_id = normalize_id(radio_id) if isinstance(radio_id, str) else ""
    if not valid_radio_id(radio_id):
        return None
    return {"id": radio_id, "endDate": normalize_expiry(expiry)}


def load_history(path=None):
    """Remembered Radio IDs, newest first, each with its expiry. Never raises.

    Every entry is ``{'id': 'XCV12345', 'endDate': '2026-10-03'}``; ``endDate`` is
    None while it is unknown.
    """
    try:
        data = json.loads(Path(path or history_path()).read_text(encoding=FILE_ENCODING))
    except (OSError, ValueError):
        return []
    rows = data.get("radioIds") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    seen, ordered = set(), []
    for raw in rows:
        entry = _history_entry(raw)
        if entry and entry["id"] not in seen:
            seen.add(entry["id"])
            ordered.append(entry)
    return ordered[:HISTORY_LIMIT]


def history_label(entry):
    """Menu text for one entry: the Radio ID plus the expiry the checker found.

    The ID is padded to a VIN width and the menu uses a monospace font, so every
    expiry lines up in one column instead of stepping in and out with the ID.
    """
    end = entry["endDate"]
    return f"{entry['id']:<{VIN_LENGTH}} - " + (f"expires {end}" if end else UNKNOWN_EXPIRY)


def save_history(entries, path=None):
    """Write the list in one atomic step. Returns True on success."""
    rows = [{"id": entry["id"], "endDate": entry["endDate"] or UNKNOWN_EXPIRY}
            for entry in entries[:HISTORY_LIMIT]]
    target = Path(path or history_path())
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(json.dumps({"radioIds": rows}, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
        return True
    except OSError:
        return False


def _write_history(entries, *, path=None):
    """Cap, save and return the list the GUI should now show."""
    ordered = entries[:HISTORY_LIMIT]
    save_history(ordered, path)
    return ordered


def remember_radio_id(radio_id, end_date=None, *, path=None):
    """Move ``radio_id`` to the front and return the new list.

    ``end_date`` is recorded when the caller knows one; passing None keeps the
    expiry already stored for that ID, so a check that fails (or has not run
    yet) never erases a date we already learned.
    """
    rid = normalize_id(radio_id or "")
    if not valid_radio_id(rid):
        return load_history(path)
    entries = load_history(path)
    previous = next((entry["endDate"] for entry in entries if entry["id"] == rid), None)
    front = {"id": rid, "endDate": normalize_expiry(end_date) or previous}
    return _write_history([front] + [e for e in entries if e["id"] != rid], path=path)


def remember_expiry(radio_id, end_date, *, path=None):
    """Record what a completed check found; None means the date is unknown."""
    rid = normalize_id(radio_id or "")
    if not valid_radio_id(rid):
        return load_history(path)
    entries = load_history(path)
    match = next((entry for entry in entries if entry["id"] == rid), None)
    if match:
        match["endDate"] = normalize_expiry(end_date)
        return _write_history(entries, path=path)
    front = {"id": rid, "endDate": normalize_expiry(end_date)}
    return _write_history([front] + entries, path=path)


# ---------------------------------------------------------------------------
# Settings: fonts, result-box size and theme
# ---------------------------------------------------------------------------
# Everything the Settings panel can change lives in one validated dict. Values
# are checked on the way in (so a hand-edited or truncated settings.json cannot
# break the layout) and normalised on the way out (so the file stays canonical).

SETTINGS_RELATIVE = "settings.json"

# Both files are meant to be hand-editable, and the editors that write them do
# not agree on a byte-order mark: Windows PowerShell and older versions of
# Notepad prepend one, which would make ``json.loads`` treat the whole file as
# corrupt. "utf-8-sig" reads either kind - it strips a BOM when present and
# behaves exactly like "utf-8" when there is none. Writing stays plain UTF-8.
FILE_ENCODING = "utf-8-sig"

# Fixed-pitch faces first - the Radio ID, the result box and the Recent menu are
# all column-aligned - then a few proportional ones for anyone who prefers them.
TEXT_FONTS = (
    "Consolas", "Cascadia Mono", "Cascadia Code", "Lucida Console", "Courier New", "Courier",
    "Segoe UI", "Tahoma", "Verdana", "Calibri", "Trebuchet MS", "Georgia", "Times New Roman",
)
INTERFACE_FONTS = (
    "Segoe UI", "Tahoma", "Verdana", "Calibri", "Trebuchet MS", "Georgia", "Consolas", "Arial",
)
MONOSPACED_FONTS = ("Consolas", "Cascadia Mono", "Cascadia Code", "Lucida Console",
                    "Courier New", "Courier")
TEXT_SIZES = tuple(range(8, 21))
INTERFACE_SIZES = tuple(range(8, 17))
BOX_ROWS = tuple(range(4, 31))
BOX_COLUMNS = tuple(range(40, 161, 4))
ACCENTS = {
    "Blue": "#3b7ddd",
    "Teal": "#0f8f86",
    "Violet": "#7b5cd6",
    "Amber": "#b8791a",
    "Rose": "#c04f7a",
}
DEFAULT_SETTINGS = {
    "textFont": "Consolas",
    "textSize": 10,
    "interfaceFont": "Segoe UI",
    "interfaceSize": 9,
    "boxRows": 8,
    "boxColumns": 80,
    "theme": "light",
    "accent": "Blue",
}

# Every status the banner can show, including the two that are not check results.
STATUS_KEYS = ("idle", "busy", "active", "today", "tomorrow", "overdue",
               "no-subscription", "error", "dates unavailable")
STATUS_TEXT = {
    "active": "ACTIVE - subscription is running",
    "today": "ACTIVE - expires TODAY",
    "tomorrow": "ACTIVE - expires TOMORROW",
    "overdue": "EXPIRED - subscription has ended",
    "no-subscription": "NO SUBSCRIPTION - nothing on file",
    "error": "CHECK FAILED",
    "dates unavailable": "ACTIVE? - dates unavailable",
}

# Themes carry their own status palette: the pastel greens and reds that read
# well on white are unreadable as-is on black, so each theme tints its own.
# ``ttk`` is the ttk theme to switch to - the native "vista" look cannot be
# recoloured, so every theme with a dark background uses "clam" instead.
THEMES = {
    "light": {
        "label": "Light",
        "ttk": "vista",
        "icon": "dark",
        "window": "#f0f0f0",
        "panel": "#f0f0f0",
        "entry_bg": "#ffffff",
        "entry_fg": "#1f1f1f",
        "text_bg": "#ffffff",
        "text_fg": "#1f1f1f",
        "muted": "#666666",
        "border": "#bcbcbc",
        "trough": "#e0e0e0",
        "button_bg": "#e8e8e8",
        "button_fg": "#1f1f1f",
        "menu_bg": "#ffffff",
        "menu_fg": "#1f1f1f",
        "menu_active_bg": "#dbe7fb",
        "menu_active_fg": "#10233f",
        "accent": "Blue",
        "status": {
            "idle": ("#eeeeee", "#333333"),
            "busy": ("#e8f0fe", "#1a3b8b"),
            "active": ("#e6f4ea", "#137333"),
            "today": ("#fef7e0", "#a06a00"),
            "tomorrow": ("#fef7e0", "#a06a00"),
            "overdue": ("#fce8e6", "#c5221f"),
            "no-subscription": ("#f1f3f4", "#5f6368"),
            "error": ("#fce8e6", "#c5221f"),
            "dates unavailable": ("#fef7e0", "#a06a00"),
        },
    },
    "dark": {
        "label": "Dark",
        "ttk": "clam",
        "icon": "light",
        "window": "#1e1f22",
        "panel": "#26282c",
        "entry_bg": "#15161a",
        "entry_fg": "#e8eaed",
        "text_bg": "#141519",
        "text_fg": "#dfe3e8",
        "muted": "#9aa0a6",
        "border": "#3c4046",
        "trough": "#33363b",
        "button_bg": "#33363c",
        "button_fg": "#eef0f2",
        "menu_bg": "#24262a",
        "menu_fg": "#e8eaed",
        "menu_active_bg": "#35435c",
        "menu_active_fg": "#ffffff",
        "accent": "Blue",
        "status": {
            "idle": ("#2a2c30", "#cfd4d9"),
            "busy": ("#1d2c47", "#9dc0ff"),
            "active": ("#12351f", "#6ddc9a"),
            "today": ("#3a2f10", "#f0c060"),
            "tomorrow": ("#3a2f10", "#f0c060"),
            "overdue": ("#3d1a17", "#ff8f85"),
            "no-subscription": ("#2a2c30", "#b3b9bf"),
            "error": ("#3d1a17", "#ff8f85"),
            "dates unavailable": ("#3a2f10", "#f0c060"),
        },
    },
    "midnight": {
        "label": "Midnight",
        "ttk": "clam",
        "icon": "light",
        "window": "#0b1220",
        "panel": "#111a2b",
        "entry_bg": "#0a1120",
        "entry_fg": "#cfe0ff",
        "text_bg": "#080e19",
        "text_fg": "#cfe0ff",
        "muted": "#7d90ad",
        "border": "#24334c",
        "trough": "#1b2740",
        "button_bg": "#1a2436",
        "button_fg": "#dbe7ff",
        "menu_bg": "#101a2c",
        "menu_fg": "#dbe7ff",
        "menu_active_bg": "#22314c",
        "menu_active_fg": "#ffffff",
        "accent": "Blue",
        "status": {
            "idle": ("#152034", "#b9c9e6"),
            "busy": ("#16274a", "#9dc0ff"),
            "active": ("#0f2f26", "#6fe3b0"),
            "today": ("#31290d", "#f2c96b"),
            "tomorrow": ("#31290d", "#f2c96b"),
            "overdue": ("#331420", "#ff9b9b"),
            "no-subscription": ("#17202f", "#a8b6c9"),
            "error": ("#331420", "#ff9b9b"),
            "dates unavailable": ("#31290d", "#f2c96b"),
        },
    },
    "slate": {
        "label": "Slate",
        "ttk": "clam",
        "icon": "light",
        "window": "#2c3037",
        "panel": "#343a43",
        "entry_bg": "#23272d",
        "entry_fg": "#e3e7ec",
        "text_bg": "#202429",
        "text_fg": "#dde2e8",
        "muted": "#98a2ad",
        "border": "#454c57",
        "trough": "#3b4149",
        "button_bg": "#3d444e",
        "button_fg": "#e8ecf1",
        "menu_bg": "#30353d",
        "menu_fg": "#e3e7ec",
        "menu_active_bg": "#46536a",
        "menu_active_fg": "#ffffff",
        "accent": "Teal",
        "status": {
            "idle": ("#2f343b", "#cbd2d9"),
            "busy": ("#1f2f45", "#a8c8ff"),
            "active": ("#16362a", "#7fe0b0"),
            "today": ("#38300f", "#ecc86a"),
            "tomorrow": ("#38300f", "#ecc86a"),
            "overdue": ("#3a1e1e", "#ff9a91"),
            "no-subscription": ("#2b3138", "#aab4bf"),
            "error": ("#3a1e1e", "#ff9a91"),
            "dates unavailable": ("#38300f", "#ecc86a"),
        },
    },
    "contrast": {
        "label": "High contrast",
        "ttk": "clam",
        "icon": "light",
        "window": "#000000",
        "panel": "#000000",
        "entry_bg": "#000000",
        "entry_fg": "#ffffff",
        "text_bg": "#000000",
        "text_fg": "#ffffff",
        "muted": "#d8d8d8",
        "border": "#ffffff",
        "trough": "#1a1a1a",
        "button_bg": "#1a1a1a",
        "button_fg": "#ffffff",
        "menu_bg": "#000000",
        "menu_fg": "#ffffff",
        "menu_active_bg": "#ffffff",
        "menu_active_fg": "#000000",
        "accent": "Amber",
        "status": {
            "idle": ("#000000", "#ffffff"),
            "busy": ("#002b4d", "#ffffff"),
            "active": ("#004d00", "#ffffff"),
            "today": ("#4d3b00", "#ffffff"),
            "tomorrow": ("#4d3b00", "#ffffff"),
            "overdue": ("#4d0000", "#ffffff"),
            "no-subscription": ("#262626", "#ffffff"),
            "error": ("#4d0000", "#ffffff"),
            "dates unavailable": ("#4d3b00", "#ffffff"),
        },
    },
    "amber": {
        "label": "Amber CRT",
        "ttk": "clam",
        "icon": "light",
        "window": "#0d0b07",
        "panel": "#15110a",
        "entry_bg": "#0a0805",
        "entry_fg": "#ffb000",
        "text_bg": "#0a0805",
        "text_fg": "#ffb000",
        "muted": "#a97c22",
        "border": "#3d3018",
        "trough": "#241c0d",
        "button_bg": "#241c0e",
        "button_fg": "#ffc857",
        "menu_bg": "#14100a",
        "menu_fg": "#ffb000",
        "menu_active_bg": "#3a2a08",
        "menu_active_fg": "#ffe6a3",
        "accent": "Amber",
        "status": {
            "idle": ("#1a1610", "#e0b055"),
            "busy": ("#2a2107", "#ffd166"),
            "active": ("#12240f", "#9cff5c"),
            "today": ("#2c2107", "#ffcc55"),
            "tomorrow": ("#2c2107", "#ffcc55"),
            "overdue": ("#2b0d0d", "#ff6b5c"),
            "no-subscription": ("#1c1a15", "#c9a35a"),
            "error": ("#2b0d0d", "#ff6b5c"),
            "dates unavailable": ("#2c2107", "#ffcc55"),
        },
    },
}


def _rgb(colour):
    """``#rrggbb`` -> (r, g, b), or None when it is not a colour."""
    text = str(colour).lstrip("#")
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return None


def _luminance(colour):
    """WCAG relative luminance: 0 (black) to 1 (white)."""
    channels = _rgb(colour)
    if channels is None:
        return 0.0
    parts = []
    for value in channels:
        share = value / 255
        parts.append(share / 12.92 if share <= 0.03928 else ((share + 0.055) / 1.055) ** 2.4)
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]


def _contrast(foreground, background):
    """WCAG contrast ratio between two colours (1.0 to 21.0).

    The themes are checked against this because a status colour that reads well
    on white can be unreadable on black - and vice versa.
    """
    first, second = _luminance(foreground), _luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _darkness(colour):
    """Perceived brightness 0-255, for "is this a dark theme?" checks."""
    channels = _rgb(colour)
    if channels is None:
        return 255
    red, green, blue = channels
    return round(0.299 * red + 0.587 * green + 0.114 * blue)


def settings_path(path=None):
    """Where user preferences live (a sibling of the recent Radio IDs)."""
    return Path(path) if path else history_path().with_name(SETTINGS_RELATIVE)


def nearest_choice(allowed, value):
    """The allowed number closest to ``value`` - used to snap box sizes."""
    return min(allowed, key=lambda option: abs(option - value))


def normalize_settings(values):
    """Coerce anything into a complete, usable settings dict.

    Unknown keys are dropped, unknown theme/accent/font values fall back to the
    defaults, and numbers are clamped into their menu and snapped to the nearest
    choice, so a stale or hand-edited file can never leave a widget unusable.
    """
    values = values if isinstance(values, dict) else {}

    def name(key, allowed, default):
        raw = values.get(key)
        if isinstance(raw, str):
            for option in allowed:
                if option.lower() == raw.strip().lower():
                    return option
        return default

    def number(key, allowed, default):
        try:
            value = int(values.get(key))
        except (TypeError, ValueError):
            return default
        return nearest_choice(allowed, min(max(value, allowed[0]), allowed[-1]))

    theme = "light"
    raw_theme = values.get("theme")
    if isinstance(raw_theme, str):
        wanted = raw_theme.strip().lower()
        for key, definition in THEMES.items():
            if wanted in (key, definition["label"].lower()):
                theme = key
                break

    return {
        "textFont": name("textFont", TEXT_FONTS, DEFAULT_SETTINGS["textFont"]),
        "textSize": number("textSize", TEXT_SIZES, DEFAULT_SETTINGS["textSize"]),
        "interfaceFont": name("interfaceFont", INTERFACE_FONTS, DEFAULT_SETTINGS["interfaceFont"]),
        "interfaceSize": number("interfaceSize", INTERFACE_SIZES, DEFAULT_SETTINGS["interfaceSize"]),
        "boxRows": number("boxRows", BOX_ROWS, DEFAULT_SETTINGS["boxRows"]),
        "boxColumns": number("boxColumns", BOX_COLUMNS, DEFAULT_SETTINGS["boxColumns"]),
        "theme": theme,
        "accent": name("accent", tuple(ACCENTS), DEFAULT_SETTINGS["accent"]),
    }


def theme_of(settings):
    """The theme definition a settings dict points at (Light when unknown)."""
    return THEMES.get((settings or {}).get("theme"), THEMES["light"])


def accent_colour(settings):
    """The chosen accent, as a colour."""
    return ACCENTS.get((settings or {}).get("accent"), ACCENTS[DEFAULT_SETTINGS["accent"]])


def load_settings(path=None):
    """Stored preferences, completed with defaults. Never raises."""
    try:
        data = json.loads(Path(path or settings_path()).read_text(encoding=FILE_ENCODING))
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)
    return normalize_settings(data)


def save_settings(values, path=None):
    """Write preferences in one atomic step. Returns True on success."""
    target = Path(path or settings_path())
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(json.dumps(normalize_settings(values), indent=2) + "\n",
                             encoding="utf-8")
        temporary.replace(target)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Extracted check logic
# (mirrors sxm_api.py: normalize_id / valid_date / subscriptions /
#  subscription_dates / due_stage / module_response / api_error)
# ---------------------------------------------------------------------------

def normalize_id(value):
    return value.strip().upper()


def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", value):
        return None
    try:
        if "T" in value:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def subscriptions(data):
    value = data.get("subscriptions")
    if not isinstance(value, list):
        nested = data.get("data")
        value = nested.get("subscriptions") if isinstance(nested, dict) else None
    if not isinstance(value, list):
        raise ValueError("Missing or malformed subscriptions")
    return value


def selected_plan(data):
    """Best (start, end, plan, subscription): the latest valid end date."""
    best = None
    for sub in subscriptions(data):
        if not isinstance(sub, dict):
            continue
        for plan in sub.get("plans") or []:
            if not isinstance(plan, dict):
                continue
            end = valid_date(plan.get("endDate"))
            if not end:
                continue
            start = valid_date(plan.get("startDate"))
            start = start if start and start <= end else None
            if best is None or end > best[1]:
                best = (start, end, plan, sub)
    return best


def subscription_dates(data):
    plan = selected_plan(data)
    return (plan[0], plan[1]) if plan else (None, None)


def due_stage(end, today=None):
    end = valid_date(end)
    if not end:
        return None
    days = (date.fromisoformat(end) - (today or date.today())).days
    return "overdue" if days < 0 else "today" if days == 0 else "tomorrow" if days == 1 else None


def module_response(data):
    module = data.get("ModuleListResponse")
    try:
        if isinstance(module, str):
            module = json.loads(module)
        return (module or {}).get("moduleList", {}).get("modules", {}).get("moduleResponse", {}).get("AccountResponse", {})
    except (ValueError, AttributeError):
        return {}


def api_error(data):
    if not isinstance(data, dict) or not data:
        return "Empty or malformed API response"
    for obj in (data, module_response(data)):
        if not isinstance(obj, dict):
            continue
        for entry in obj.get("errors") or []:
            code = entry.get("code") if isinstance(entry, dict) else None
            if code and re.fullmatch(r"[\w-]{1,80}", str(code)):
                return str(code)
        if obj.get("errors") or obj.get("error") or obj.get("success") is False:
            return "API rejected request"
        if obj.get("errorCode") not in (None, "", 0, "0"):
            code = str(obj["errorCode"])
            return code if re.fullmatch(r"[\w-]{1,80}", code) else "API rejected request"
        if obj.get("opstatus") not in (None, 0, "0"):
            return "API operation failed"
    return None


# ---------------------------------------------------------------------------
# Check result
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    radio_id: str
    ok: bool
    status: str  # active | today | tomorrow | overdue | no-subscription | error
    region: str = None
    start_date: str = None
    end_date: str = None
    days_remaining: int = None
    plan_label: str = None
    plan_type: str = None
    account_status: str = None
    is_demo: bool = False
    error: str = None
    diagnostics: list = field(default_factory=list)

    @property
    def is_trial(self):
        return self.is_demo or (self.plan_type or "").upper() == "DEMO"

    def format(self, verbose=False):
        if self.status == "error":
            head = f"CHECK FAILED - {self.radio_id}"
        elif self.status == "no-subscription":
            head = f"NO SUBSCRIPTION FOUND - {self.radio_id}"
        elif self.status == "active":
            head = f"ACTIVE - {self.radio_id} (subscription running)"
        elif self.status == "overdue":
            head = f"EXPIRED - {self.radio_id} (subscription ended)"
        else:
            head = f"{self.status.upper()} - {self.radio_id}"
        lines = [head]
        if self.region:
            lines.append(f"  Market:      {REGION_NAMES.get(self.region, self.region)}")
        if self.plan_label or self.plan_type:
            kind = "Trial / Demo" if self.is_trial else (self.plan_type or "Paid / Other")
            lines.append(f"  Plan:        {self.plan_label or '(unnamed)'}  [{kind}]")
        if self.account_status:
            lines.append(f"  Account:     {self.account_status}")
        lines.append(f"  Start:       {self.start_date or 'unknown'}")
        lines.append(f"  End:         {self.end_date or 'unknown'}")
        if self.days_remaining is not None:
            lines.append(f"  Days left:   {self.days_remaining}")
        if self.error:
            lines.append(f"  Error:       {self.error}")
        if verbose and self.diagnostics:
            lines.append("  Diagnostics:")
            lines.extend(f"    - {d}" for d in self.diagnostics)
        return "\n".join(lines)


def check_subscription(radio_id, prefer="US", session=None, timeout=30, regions=None):
    """Return a CheckResult for ``radio_id``.

    ``prefer`` is "US" (default; United States first, then Canada) or "CA"
    (Canada first, then United States), matching the CLI's --ca switch. Pass an
    explicit ``regions`` list (e.g. ``["com"]``) to pin the market order.
    """
    rid = normalize_id(radio_id or "")
    diagnostics = []
    if not re.fullmatch(r"[A-Z0-9]{8,17}", rid):
        return CheckResult(rid, False, "error", error="Invalid Radio ID",
                           diagnostics=["Radio IDs are 8 characters or 12 digits (VINs are 17)."])

    session = session or requests.Session()
    if regions is None:
        regions = ["ca", "com"] if prefer == "CA" else ["com", "ca"]
    failures = []

    for region in regions:
        url = CHECK_URL.format(region=region)
        try:
            response = session.post(url, json={"radioId": rid}, timeout=timeout)
            diagnostics.append(f"POST {url} -> HTTP {response.status_code}")
            if not response.ok:
                raise ValueError(f"HTTP {response.status_code}")
            try:
                data = response.json()
            except ValueError:
                raise ValueError("Invalid JSON response") from None
            if not isinstance(data, dict):
                raise ValueError("Malformed API response")
            error = api_error(data)
            if error:
                raise ValueError(error)
            subs = subscriptions(data)
            if not subs:
                diagnostics.append(f"{region}: no subscriptions; trying next market")
                continue

            plan = selected_plan(data)
            start = end = None
            plan_label = plan_type = account_status = None
            if plan:
                start, end, plan_dict, sub = plan
                plan_label = plan_dict.get("label") or plan_dict.get("code")
                plan_type = plan_dict.get("type")
                account_status = sub.get("status")
            stage = due_stage(end)
            status = stage or ("active" if end else "dates unavailable")
            days = None
            if end:
                days = (date.fromisoformat(end) - date.today()).days
            nested = data.get("data") if isinstance(data.get("data"), dict) else {}
            is_demo = bool(nested.get("hasDemoSubscription")) or (plan_type or "").upper() == "DEMO"
            return CheckResult(rid, True, status, region, start, end, days,
                               plan_label, plan_type, account_status, is_demo,
                               diagnostics=diagnostics)
        except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
            error = str(exc) if isinstance(exc, ValueError) else "Network or malformed response"
            diagnostics.append(f"{region}: {error}")
            failures.append(f"{REGION_NAMES[region]}: {error}")

    if failures:
        return CheckResult(rid, False, "error", error="; ".join(failures), diagnostics=diagnostics)
    return CheckResult(rid, True, "no-subscription", diagnostics=diagnostics)


# ---------------------------------------------------------------------------
# Offline self-test (used to verify the frozen .exe has no missing pieces)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.ok = data, status, status < 400

    def json(self):
        return self._data


class _FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = iter(responses), []

    def post(self, url, **kwargs):
        self.calls.append(url)
        return next(self.responses)


def run_selftest(out_path=None):
    """Exercise the parsing/region logic with fictional data. 0 = pass."""
    checks = []

    def expect(name, condition, detail=""):
        checks.append((name, bool(condition), detail))

    us_active = {"data": {"hasDemoSubscription": True, "subscriptions": [{
        "status": "Active",
        "plans": [{"code": "Fixed Demo - Premier - 3mo", "label": "Fixed Demo - Premier - 3mo (Used Car)",
                   "type": "DEMO", "startDate": "2026-06-29T00:00:00-04:00",
                   "endDate": "2099-10-01T00:00:00-04:00"}]}]}}

    result = check_subscription(" demo0001 ", session=_FakeSession(_FakeResponse(us_active)))
    expect("active subscription detected", result.ok and result.status == "active", result.format())
    expect("trial/demo recognised", result.is_trial and result.region == "com")
    expect("dates parsed", result.start_date == "2026-06-29" and result.end_date == "2099-10-01")

    expired = {"subscriptions": [{"status": "Cancelled",
               "plans": [{"type": "DEMO", "startDate": "2020-01-01", "endDate": "2020-04-01"}]}]}
    result = check_subscription("DEMO0001", session=_FakeSession(_FakeResponse(expired)))
    expect("expired subscription flagged", result.ok and result.status == "overdue")

    empty = {"subscriptions": []}
    result = check_subscription("DEMO0001", session=_FakeSession(_FakeResponse(empty), _FakeResponse(empty)))
    expect("no subscription found", result.ok and result.status == "no-subscription")

    session = _FakeSession(_FakeResponse({}, 503), _FakeResponse(us_active))
    result = check_subscription("DEMO0001", prefer="CA", session=session)
    expect("Canada-first order then US fallback",
           session.calls[0].startswith("https://care.siriusxm.ca/")
           and session.calls[1].startswith("https://care.siriusxm.com/")
           and result.ok and result.region == "com", str(session.calls))

    result = check_subscription("bad", session=_FakeSession())
    expect("invalid id rejected", not result.ok and result.status == "error")

    expect("US signal page is the official refresh link",
           signal_url("com") == "https://www.siriusxm.com/refresh")
    expect("Canada signal page is the official refresh link",
           signal_url("ca") == "https://www.siriusxm.ca/refresh")
    expect("window icon resource is bundled", window_icon() is not None)
    frames = ico_frame_sizes(window_icon()) if window_icon() else []
    expect("icon holds native 16px and 32px frames",
           {(16, 16), (32, 32)}.issubset(set(frames)),
           f"frames={sorted({w for w, _h in frames})}")
    expect("the Settings gear is bundled in both tones",
           all(settings_icon(tone, 1) is not None for tone in ("dark", "light")),
           str({tone: bool(settings_icon(tone, 1)) for tone in ("dark", "light")}))
    expect("the gear is a real 20px png in both tones",
           all(png_size(settings_icon(tone, 1)) == (20, 20) for tone in ("dark", "light")),
           str({tone: png_size(settings_icon(tone, 1)) for tone in ("dark", "light")}))
    expect("a scaled screen is given the 32px gear",
           all(png_size(settings_icon(tone, 2)) == (32, 32) for tone in ("dark", "light")),
           str({tone: png_size(settings_icon(tone, 2)) for tone in ("dark", "light")}))
    expect("a missing gear never breaks the button",
           settings_icon("nonsense", 1) is None and png_size("no-such-file.png") is None)
    expect("history path is an absolute per-user file",
           history_path().is_absolute() and history_path().suffix == ".json",
           str(history_path()))
    unknown = history_label({"id": "ABC12345", "endDate": None})
    known = history_label({"id": "ABC12345", "endDate": "2026-10-03"})
    expect("an unknown expiry reads as Unknown", unknown.split(" - ")[-1] == UNKNOWN_EXPIRY, unknown)
    expect("a known expiry is shown with its date",
           known.split(" - ")[-1] == "expires 2026-10-03", known)
    expect("the id is padded to VIN width so the dates line up in a column",
           all(len(history_label(entry).split(" - ")[0]) == VIN_LENGTH
               for entry in ({"id": "AB12CD34", "endDate": None},
                             {"id": "Z" * VIN_LENGTH, "endDate": "2026-10-03"})),
           repr(unknown))
    # A made-up 17-character id: the repository holds no real Radio IDs or VINs.
    sample_vin = "DEMOVIN0000000001"
    expect("a full VIN is the widest valid id, so the field needs no more room",
           valid_radio_id(sample_vin) and len(sample_vin) == VIN_LENGTH
           and not valid_radio_id(sample_vin + "2"),
           f"VIN_LENGTH={VIN_LENGTH}")

    def ids(entries):
        return [entry["id"] for entry in entries]

    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "history.json"
        expect("history starts empty", load_history(store) == [], str(load_history(store)))
        remember_radio_id("abc12345", path=store)
        remember_radio_id("ZZ999999", "2026-10-03", path=store)
        expect("history keeps the newest first",
               ids(load_history(store)) == ["ZZ999999", "ABC12345"], str(load_history(store)))
        expect("the expiry is stored with the id",
               load_history(store)[0]["endDate"] == "2026-10-03", str(load_history(store)))
        expect("an unchecked id has no expiry yet",
               load_history(store)[1]["endDate"] is None, str(load_history(store)))
        remember_radio_id("abc12345", "2020-01-02", path=store)
        expect("reusing an id moves it to the front with its date",
               [(entry["id"], entry["endDate"]) for entry in load_history(store)]
               == [("ABC12345", "2020-01-02"), ("ZZ999999", "2026-10-03")],
               str(load_history(store)))
        remember_radio_id("abc12345", path=store)
        expect("a check with no date keeps the known expiry",
               load_history(store)[0] == {"id": "ABC12345", "endDate": "2020-01-02"},
               str(load_history(store)))
        remember_expiry("abc12345", "2027-03-04", path=store)
        expect("a completed check updates the expiry",
               load_history(store)[0] == {"id": "ABC12345", "endDate": "2027-03-04"},
               str(load_history(store)))
        remember_expiry("NEW12345", "2028-01-01", path=store)
        expect("an id first seen by a check is added with its date",
               load_history(store)[0] == {"id": "NEW12345", "endDate": "2028-01-01"},
               str(load_history(store)))
        remember_expiry("abc12345", None, path=store)
        expect("an unreadable date is cleared, not guessed",
               load_history(store)[1] == {"id": "ABC12345", "endDate": None},
               str(load_history(store)))
        remember_expiry("abc12345", "not a date", path=store)
        expect("a malformed date is treated as unknown",
               load_history(store)[1]["endDate"] is None, str(load_history(store)))
        expect("the file spells an unknown expiry as Unknown",
               json.loads(store.read_text(encoding="utf-8"))["radioIds"][1]["endDate"]
               == UNKNOWN_EXPIRY, store.read_text(encoding="utf-8"))
        remember_radio_id("bad", path=store)
        expect("invalid ids are not remembered",
               ids(load_history(store)) == ["NEW12345", "ABC12345", "ZZ999999"],
               str(load_history(store)))
        expect("a missing history file is not an error",
               load_history(Path(tmp) / "absent.json") == [])
        store.write_text("{ not json", encoding="utf-8")
        expect("corrupt history is ignored", load_history(store) == [], str(load_history(store)))
        store.write_text(json.dumps({"radioIds": ["good1234", 42, None, "GOOD1234", "short"]}),
                         encoding="utf-8")
        expect("older string entries still load, with no expiry",
               load_history(store) == [{"id": "GOOD1234", "endDate": None}],
               str(load_history(store)))
        store.write_text(json.dumps({"radioIds": [{"radioId": "ALT12345", "end": "2029-05-06"}]}),
                         encoding="utf-8")
        expect("alternative key names still load",
               load_history(store) == [{"id": "ALT12345", "endDate": "2029-05-06"}],
               str(load_history(store)))
        store.write_bytes(b"\xef\xbb\xbf" + json.dumps(
            {"radioIds": [{"id": "BOM12345", "endDate": "2030-01-02"}]}).encode("utf-8"))
        expect("a file an editor saved with a byte-order mark still loads",
               load_history(store) == [{"id": "BOM12345", "endDate": "2030-01-02"}],
               str(load_history(store)))

        store.unlink()
        for index in range(HISTORY_LIMIT + 5):
            remember_radio_id(f"SHUNT{index:05d}", path=store)
        kept = load_history(store)
        expect("history is capped", len(kept) == HISTORY_LIMIT, f"len={len(kept)}")
        expect("the newest id survives the cap",
               kept[0]["id"] == f"SHUNT{HISTORY_LIMIT + 4:05d}", str(ids(kept)[:3]))
        expect("the oldest ids are dropped", "SHUNT00000" not in ids(kept))
        expect("history is written as valid json",
               all(isinstance(row, dict) and "id" in row and "endDate" in row
                   for row in json.loads(store.read_text(encoding="utf-8")).get("radioIds")))
        expect("no leftover temp file",
               not (Path(tmp) / "history.json.tmp").exists())

    expect("settings sit beside the recent list",
           settings_path().parent == history_path().parent
           and settings_path().name == SETTINGS_RELATIVE,
           str(settings_path()))
    expect("every theme defines the same keys",
           all(set(theme) == set(THEMES["light"]) for theme in THEMES.values()),
           str({name: sorted(set(theme) ^ set(THEMES["light"]))
                for name, theme in THEMES.items() if set(theme) != set(THEMES["light"])}))
    expect("every theme styles every status",
           all(set(theme["status"]) == set(STATUS_KEYS) for theme in THEMES.values()),
           str([name for name, theme in THEMES.items()
                if set(theme["status"]) != set(STATUS_KEYS)]))
    hex_colour = re.compile(r"#[0-9a-fA-F]{6}\Z")

    def theme_colours(theme):
        for key, value in theme.items():
            if key == "status":
                for status, pair in value.items():
                    yield f"status.{status}", pair
            elif isinstance(value, str) and value.startswith("#"):
                yield key, (value,)

    off_colour = [f"{name}.{key}" for name, theme in THEMES.items()
                  for key, values in theme_colours(theme)
                  if not all(hex_colour.match(value) for value in values)]
    expect("every theme colour is a #rrggbb value", not off_colour, str(off_colour))
    expect("every theme asks for a ttk look and a glyph tone",
           all(theme["ttk"] in ("vista", "clam") and theme["icon"] in ("dark", "light")
               for theme in THEMES.values()))
    expect("every theme suggests an accent that exists",
           all(theme["accent"] in ACCENTS for theme in THEMES.values()),
           str([name for name, theme in THEMES.items() if theme["accent"] not in ACCENTS]))
    expect("the dark themes use clam, the only look that can be recoloured",
           all(theme["ttk"] == "clam" for name, theme in THEMES.items()
               if _darkness(theme["window"]) < 128),
           str({name: theme["ttk"] for name, theme in THEMES.items()
                if _darkness(theme["window"]) < 128}))
    expect("every status colour has readable contrast",
           all(_contrast(foreground, background) >= 4.0 for theme in THEMES.values()
               for background, foreground in theme["status"].values()),
           str(round(min(_contrast(f, b) for theme in THEMES.values()
                         for b, f in theme["status"].values()), 2)))
    expect("body text keeps a usable contrast against its own background",
           all(_contrast(theme["text_fg"], theme["text_bg"]) >= 4.5 for theme in THEMES.values()),
           str({name: round(_contrast(theme["text_fg"], theme["text_bg"]), 1)
                for name, theme in THEMES.items()}))
    expect("the text font menu offers monospaced and proportional faces",
           len(set(MONOSPACED_FONTS) & set(TEXT_FONTS)) >= 4
           and len(set(TEXT_FONTS) - set(MONOSPACED_FONTS)) >= 4,
           str(TEXT_FONTS))
    expect("the font menus are unique and usable",
           len(set(TEXT_FONTS)) == len(TEXT_FONTS) and len(set(INTERFACE_FONTS)) == len(INTERFACE_FONTS)
           and all(isinstance(name, str) and name.strip() for name in TEXT_FONTS + INTERFACE_FONTS))
    expect("the defaults are all on offer",
           DEFAULT_SETTINGS["textFont"] in TEXT_FONTS
           and DEFAULT_SETTINGS["interfaceFont"] in INTERFACE_FONTS
           and DEFAULT_SETTINGS["textSize"] in TEXT_SIZES
           and DEFAULT_SETTINGS["interfaceSize"] in INTERFACE_SIZES
           and DEFAULT_SETTINGS["boxRows"] in BOX_ROWS
           and DEFAULT_SETTINGS["boxColumns"] in BOX_COLUMNS
           and DEFAULT_SETTINGS["theme"] in THEMES
           and DEFAULT_SETTINGS["accent"] in ACCENTS)
    expect("the settings menus are sorted smallest first",
           list(TEXT_SIZES) == sorted(TEXT_SIZES) and list(BOX_ROWS) == sorted(BOX_ROWS)
           and list(BOX_COLUMNS) == sorted(BOX_COLUMNS))

    expect("junk settings fall back to the defaults",
           normalize_settings(None) == DEFAULT_SETTINGS
           and normalize_settings("not a dict") == DEFAULT_SETTINGS
           and normalize_settings({"textSize": "big", "theme": "nope", "accent": None,
                                   "textFont": 12}) == DEFAULT_SETTINGS,
           str(normalize_settings({"textSize": "big"})))
    expect("settings survive a round trip through json",
           normalize_settings(json.loads(json.dumps(DEFAULT_SETTINGS))) == DEFAULT_SETTINGS)
    expect("font names are matched case-insensitively",
           normalize_settings({"textFont": "verdana"})["textFont"] == "Verdana"
           and normalize_settings({"interfaceFont": "tahoma"})["interfaceFont"] == "Tahoma")
    expect("themes are accepted by id or by label",
           normalize_settings({"theme": "Dark"})["theme"] == "dark"
           and normalize_settings({"theme": "AMBER CRT"})["theme"] == "amber"
           and normalize_settings({"theme": "high contrast"})["theme"] == "contrast",
           str(normalize_settings({"theme": "Amber CRT"})["theme"]))
    expect("accents are accepted case-insensitively",
           normalize_settings({"accent": "violet"})["accent"] == "Violet")
    expect("numbers given as text still work",
           normalize_settings({"textSize": "14", "boxRows": "20"})["textSize"] == 14
           and normalize_settings({"boxRows": "20"})["boxRows"] == 20)
    expect("oversized values are clamped into the menu",
           normalize_settings({"textSize": 999})["textSize"] == TEXT_SIZES[-1]
           and normalize_settings({"interfaceSize": -5})["interfaceSize"] == INTERFACE_SIZES[0]
           and normalize_settings({"boxRows": 1})["boxRows"] == BOX_ROWS[0],
           str(normalize_settings({"textSize": 999})["textSize"]))
    expect("box columns snap to the nearest offered size",
           all(normalize_settings({"boxColumns": wanted})["boxColumns"] in BOX_COLUMNS
               for wanted in range(1, 400))
           and abs(normalize_settings({"boxColumns": 87})["boxColumns"] - 87) <= 2,
           str(normalize_settings({"boxColumns": 87})["boxColumns"]))
    expect("unknown keys are dropped, not carried",
           set(normalize_settings({"theme": "dark", "nonsense": 1, "colour": "red"}))
           == set(DEFAULT_SETTINGS))
    expect("themes resolve by id, with Light as the fallback",
           theme_of({"theme": "midnight"})["label"] == "Midnight"
           and theme_of({"theme": "gone"}) is THEMES["light"] and theme_of(None) is THEMES["light"])
    expect("the accent resolves to a colour, with the default as the fallback",
           accent_colour({"accent": "Teal"}) == ACCENTS["Teal"]
           and accent_colour({}) == ACCENTS[DEFAULT_SETTINGS["accent"]])
    expect("every check status has wording for the banner",
           set(STATUS_TEXT) | {"idle", "busy"} == set(STATUS_KEYS),
           str(set(STATUS_KEYS) ^ (set(STATUS_TEXT) | {"idle", "busy"})))
    expect("nearest_choice picks the closest option",
           nearest_choice((40, 44, 48), 43) == 44 and nearest_choice((8, 10, 12), 9) == 8)

    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "settings.json"
        expect("settings start at the defaults", load_settings(store) == DEFAULT_SETTINGS,
               str(load_settings(store)))
        save_settings({"textFont": "Verdana", "textSize": 16, "boxRows": 20,
                       "boxColumns": 100, "theme": "amber", "accent": "Rose"}, store)
        saved = load_settings(store)
        expect("a full set of settings round-trips through the file",
               saved["textFont"] == "Verdana" and saved["textSize"] == 16
               and saved["boxRows"] == 20 and saved["boxColumns"] == 100
               and saved["theme"] == "amber" and saved["accent"] == "Rose", str(saved))
        expect("saved settings keep every key", set(saved) == set(DEFAULT_SETTINGS), str(sorted(saved)))
        expect("the file is written as plain readable json",
               isinstance(json.loads(store.read_text(encoding="utf-8")).get("theme"), str))
        expect("the file holds no settings we did not ask for",
               json.loads(store.read_text(encoding="utf-8")).get("secret") is None)
        store.write_text("{ not json", encoding="utf-8")
        expect("corrupt settings fall back to the defaults", load_settings(store) == DEFAULT_SETTINGS)
        store.write_bytes(b"\xef\xbb\xbf" + json.dumps(
            {"theme": "dark", "textSize": 14}).encode("utf-8"))
        expect("a file an editor saved with a byte-order mark still loads",
               load_settings(store)["theme"] == "dark" and load_settings(store)["textSize"] == 14,
               str(load_settings(store)))
        store.write_text(json.dumps({"theme": "dark", "textSize": 999, "textFont": "Wingdings"}),
                         encoding="utf-8")
        pulled_back = load_settings(store)
        expect("values in a stale file are pulled back into range",
               pulled_back["textSize"] == TEXT_SIZES[-1] and pulled_back["theme"] == "dark"
               and pulled_back["textFont"] == DEFAULT_SETTINGS["textFont"], str(pulled_back))
        expect("a missing settings file is not an error",
               load_settings(Path(tmp) / "absent.json") == DEFAULT_SETTINGS)
        expect("no leftover temp file", not (Path(tmp) / "settings.json.tmp").exists())
        expect("the settings file is not the history file",
               settings_path(store).name == SETTINGS_RELATIVE
               and settings_path().name != history_path().name,
               f"{settings_path().name} vs {history_path().name}")

    try:
        source = Path(__file__).read_text(encoding="utf-8")
        expect("no credential-looking literals in the source",
               not re.findall(r"['\"][0-9a-fA-F]{32}['\"]", source))
        needle = "sele" "nium"      # split so this check cannot match its own source
        expect("no browser-automation dependency in the source",
               needle not in source.lower())
    except OSError:
        pass  # frozen build: source is not on disk, verified separately

    passed = all(ok for _, ok, _ in checks)
    lines = [f"selftest: {'PASS' if passed else 'FAIL'} ({sum(1 for _, ok, _ in checks if ok)}/{len(checks)})"]
    for name, ok, detail in checks:
        lines.append(f"  [{'ok' if ok else 'FAIL'}] {name}")
        if not ok and detail:
            lines.append(f"        {detail}")
    report = "\n".join(lines)
    try:
        print(report)
    except Exception:
        pass
    if out_path:
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(report + "\n")
    return 0 if passed else 1


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def _enable_windows_dpi():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class CheckerApp:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.results = queue.Queue()
        self.last = None
        self.busy = False
        self.settings = load_settings()      # fonts, result-box size, theme
        self.theme = theme_of(self.settings)
        self.dialog = None
        self.images = {}                     # Tk drops PhotoImages with no reference

        root.title("SiriusXM Radio ID Checker")
        root.minsize(560, 470)
        apply_window_icon(root)          # window title bar, taskbar and dialogs

        main = ttk.Frame(root, padding=14)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        # Every font and colour below is set by apply_settings(), so the panel can
        # change them all at once.
        self.header = ttk.Label(main, text="SiriusXM Radio ID Checker")
        self.header.grid(row=0, column=0, sticky="w")
        self.subtitle = ttk.Label(main, text="Check a subscription and request a refresh signal (US + Canada).")
        self.subtitle.grid(row=1, column=0, sticky="w", pady=(2, 10))

        form = ttk.Frame(main)
        form.grid(row=2, column=0, sticky="ew")
        # The Recent box takes whatever width the row has left, so it grows with
        # the window instead of the Radio ID field, which only needs a VIN.
        form.columnconfigure(4, weight=1)
        ttk.Label(form, text="Radio ID:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.radio_var = tk.StringVar()
        # One spare character past a full VIN, and no wider: the field cannot
        # hold anything longer anyway.
        self.radio_entry = ttk.Entry(form, textvariable=self.radio_var, width=VIN_LENGTH + 1)
        self.radio_entry.grid(row=0, column=1, sticky="w")
        self.radio_entry.bind("<Return>", lambda _e: self.start_check())
        self.radio_entry.bind("<KeyRelease>", self._uppercase)

        ttk.Label(form, text="Market order:").grid(row=0, column=2, sticky="w", padx=(12, 8))
        self.region_var = tk.StringVar(value="Auto (US, then Canada)")
        self.region_box = ttk.Combobox(form, textvariable=self.region_var, state="readonly", width=22,
                                       values=("Auto (US, then Canada)", "Canada first", "US only", "Canada only"))
        self.region_box.grid(row=0, column=3, sticky="w")

        self.history = load_history()
        self.history_button = ttk.Menubutton(form, text="Recent \u25be", width=RECENT_WIDTH)
        self.history_menu = tk.Menu(self.history_button, tearoff=False)
        self.history_button.configure(menu=self.history_menu)
        self.history_button.grid(row=0, column=4, sticky="ew", padx=(12, 0))
        self._refresh_history_menu()

        actions = ttk.Frame(main)
        actions.grid(row=3, column=0, sticky="ew", pady=10)
        self.check_button = ttk.Button(actions, text="Check Subscription", command=self.start_check)
        self.check_button.pack(side="left")
        ttk.Button(actions, text="Request Signal View Web",
                   command=self.open_signal_page).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Clear", command=self.clear).pack(side="left", padx=6)
        ttk.Button(actions, text="Copy result", command=self.copy_result).pack(side="left")
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=120)
        self.progress.pack(side="right")
        self.settings_button = ttk.Button(actions, text="Settings", compound="left",
                                          command=self.open_settings)
        self.settings_button.pack(side="right", padx=(0, 12))

        banner = ttk.Frame(main)
        banner.grid(row=4, column=0, sticky="nsew")
        banner.columnconfigure(0, weight=1)
        banner.rowconfigure(1, weight=1)
        self.status_label = tk.Label(banner, text="Enter a Radio ID and press Check.",
                                     anchor="w", padx=12, pady=10)
        self.status_label.grid(row=0, column=0, sticky="ew")

        self.detail = tk.Text(banner, wrap="word", borderwidth=0, relief="flat")
        self.detail.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.detail.configure(state="disabled")

        footer = ttk.Frame(main)
        footer.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self.hint = ttk.Label(footer, text="check + signal request only - never renews or changes a subscription")
        self.hint.pack(side="left")

        self.apply_settings(save=False)   # paints the stored theme, fonts and box size
        self.radio_entry.focus_set()
        self._poll()

    def _uppercase(self, event):
        if event.keysym in ("BackSpace", "Delete", "Left", "Right", "Up", "Down", "Home", "End", "Tab"):
            return
        value = self.radio_var.get()
        upper = value.upper()
        if value != upper:
            self.radio_var.set(upper)

    def _set_status(self, text, bg, fg):
        self.status_label.configure(text=text, bg=bg, fg=fg)

    def _status_colours(self, key):
        """The active theme's (background, foreground) for one status."""
        statuses = self.theme["status"]
        return statuses.get(key, statuses["idle"])

    def _accent(self):
        return accent_colour(self.settings)

    def _ui_font(self, extra=0, weight=None):
        """A font tuple for the chrome, scaled from the chosen interface size."""
        size = max(7, self.settings["interfaceSize"] + extra)
        return (self.settings["interfaceFont"], size) + ((weight,) if weight else ())

    def applied_theme(self):
        """The colours currently in force (used by the Settings preview)."""
        return self.theme

    def _display_scale(self):
        """1 on a normal screen, 2 on a 200%-scaled one - picks the icon art."""
        try:
            return round(self.root.winfo_fpixels("1i") / 96)
        except Exception:
            return 1

    def _style_ttk(self):
        """Point ttk at the theme. Only clam can be recoloured."""
        style = self.ttk.Style()
        wanted = self.theme["ttk"]
        if wanted == "vista" and sys.platform != "win32":
            wanted = "clam"
        try:
            style.theme_use(wanted)
        except Exception:
            return
        try:
            # A wide box only reads as a drop-down if its label sits on the left;
            # ttk exposes that through the style rather than the widget.
            style.configure("TMenubutton", anchor="w")
            style.configure(".", font=(self.settings["interfaceFont"],
                                       self.settings["interfaceSize"]))
        except Exception:
            pass
        if style.theme_use() != "clam":
            return          # the native look cannot be recoloured, so leave it be
        theme = self.theme
        accent = self._accent()
        style.configure(".", background=theme["panel"], foreground=theme["text_fg"],
                        fieldbackground=theme["entry_bg"], bordercolor=theme["border"],
                        troughcolor=theme["trough"], focuscolor=accent)
        style.configure("TFrame", background=theme["panel"])
        style.configure("TLabel", background=theme["panel"], foreground=theme["text_fg"])
        style.configure("TButton", background=theme["button_bg"], foreground=theme["button_fg"],
                        bordercolor=theme["border"], lightcolor=theme["button_bg"],
                        darkcolor=theme["button_bg"], focuscolor=theme["border"], padding=(8, 4))
        style.map("TButton",
                  background=[("active", theme["border"]), ("pressed", theme["border"]),
                              ("disabled", theme["panel"])],
                  foreground=[("disabled", theme["muted"])])
        style.configure("TMenubutton", background=theme["button_bg"], foreground=theme["button_fg"],
                        bordercolor=theme["border"], arrowcolor=theme["button_fg"], padding=(6, 4))
        style.configure("TEntry", fieldbackground=theme["entry_bg"], foreground=theme["entry_fg"],
                        bordercolor=theme["border"], insertcolor=theme["entry_fg"])
        style.map("TEntry", bordercolor=[("focus", accent)])
        style.configure("TCombobox", fieldbackground=theme["entry_bg"], background=theme["button_bg"],
                        foreground=theme["entry_fg"], arrowcolor=theme["button_fg"],
                        bordercolor=theme["border"], lightcolor=theme["border"],
                        darkcolor=theme["border"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", theme["entry_bg"]), ("focus", theme["entry_bg"])],
                  foreground=[("readonly", theme["entry_fg"])],
                  bordercolor=[("focus", accent)])
        style.configure("TProgressbar", background=accent, troughcolor=theme["trough"],
                        bordercolor=theme["trough"], lightcolor=accent, darkcolor=accent)
        # A combobox's pop-down is a classic Listbox, so it is themed by options.
        self.root.option_add("*TCombobox*Listbox.background", theme["menu_bg"])
        self.root.option_add("*TCombobox*Listbox.foreground", theme["menu_fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", theme["menu_active_bg"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", theme["menu_active_fg"])

    def _style_menu(self):
        """Colour the Recent drop-down, or hand it back to Windows colours."""
        if self.theme["ttk"] == "clam":
            theme = self.theme
            self.history_menu.configure(background=theme["menu_bg"], foreground=theme["menu_fg"],
                                        activebackground=theme["menu_active_bg"],
                                        activeforeground=theme["menu_active_fg"],
                                        borderwidth=0, activeborderwidth=1)
        else:
            self.history_menu.configure(background="SystemMenu", foreground="SystemMenuText",
                                        activebackground="SystemHighlight",
                                        activeforeground="SystemHighlightText",
                                        borderwidth=1, activeborderwidth=1)

    def _set_settings_icon(self):
        """Swap the gear for the tone that reads on this theme."""
        path = settings_icon(self.theme["icon"], self._display_scale())
        image = None
        if path:
            try:
                image = self.tk.PhotoImage(file=str(path))
            except Exception:
                image = None
        if image is None:                       # never break the button over art
            self.settings_button.configure(image="", compound="none")
            return
        self.images["settings"] = image
        self.settings_button.configure(image=image, compound="left")

    def apply_settings(self, settings=None, save=True):
        """Push settings into every widget, then store them.

        Called with no argument to paint the stored set at start-up, and with a
        new dict from the Settings panel, which applies each choice live.
        """
        before = dict(self.settings)
        if settings is not None:
            self.settings = normalize_settings(settings)
            self.theme = theme_of(self.settings)
        theme, ui = self.theme, self.settings

        self._style_ttk()
        self._style_menu()
        self.root.configure(bg=theme["window"])
        self.header.configure(font=self._ui_font(6, "bold"), foreground=self._accent())
        self.subtitle.configure(font=self._ui_font(), foreground=theme["muted"])
        self.hint.configure(font=self._ui_font(-1), foreground=theme["muted"])
        # Only the font here: a ttk entry takes its field colours (and its caret)
        # from the style, and rejects the classic -insertbackground option.
        self.radio_entry.configure(font=(ui["textFont"], ui["textSize"] + 2))
        self.history_menu.configure(font=(ui["textFont"], max(7, ui["textSize"] - 1)))
        self.detail.configure(font=(ui["textFont"], ui["textSize"]),
                              height=ui["boxRows"], width=ui["boxColumns"],
                              background=theme["text_bg"], foreground=theme["text_fg"],
                              insertbackground=theme["text_fg"], highlightthickness=1,
                              highlightbackground=theme["border"], highlightcolor=self._accent())
        self.status_label.configure(font=self._ui_font(4, "bold"))
        self._set_settings_icon()
        if (ui["boxRows"], ui["boxColumns"]) != (before["boxRows"], before["boxColumns"]):
            self.root.geometry("")      # let the result box actually take its new size
        if isinstance(self.last, CheckResult):
            self._paint(self.last)      # repaint the banner in the new palette
        else:
            self._set_status("Enter a Radio ID and press Check.", *self._status_colours("idle"))
        if self.dialog is not None and self.dialog.alive():
            self.dialog.refresh()
        if save:
            save_settings(self.settings)
        return self.settings

    def open_settings(self):
        """Open the Settings panel, or raise it when it is already up."""
        if self.dialog is not None and self.dialog.alive():
            self.dialog.focus()
            return self.dialog
        self.dialog = SettingsDialog(self)
        return self.dialog

    def _set_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def _refresh_history_menu(self):
        """Rebuild the Recent dropdown from the remembered Radio IDs."""
        self.history_menu.delete(0, "end")
        if not self.history:
            self.history_menu.add_command(label="(no recent Radio IDs)", state="disabled")
            return
        for entry in self.history:
            self.history_menu.add_command(label=history_label(entry),
                                          command=lambda rid=entry["id"]: self._use_recent(rid))
        self.history_menu.add_separator()
        self.history_menu.add_command(label="Clear history", command=self.clear_history)

    def _use_recent(self, radio_id):
        """Load a remembered Radio ID into the field, ready to run."""
        self.radio_var.set(radio_id)
        self.radio_entry.icursor("end")
        self.radio_entry.focus_set()

    def _remember(self, radio_id, end_date=None):
        """Record an ID (with its expiry when known) and refresh the menu."""
        self.history = remember_radio_id(radio_id, end_date)
        self._refresh_history_menu()

    def _remember_expiry(self, radio_id, end_date):
        """Store what a finished check found for an ID."""
        self.history = remember_expiry(radio_id, end_date)
        self._refresh_history_menu()

    def clear_history(self):
        self.history = []
        save_history([])
        self._refresh_history_menu()

    def clear(self):
        self.radio_var.set("")
        self.last = None
        self._set_status("Enter a Radio ID and press Check.", *self._status_colours("idle"))
        self._set_detail("")
        self.radio_entry.focus_set()

    def copy_result(self):
        if not self.last:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last.format())
        self._set_status("Result copied to clipboard.", *self._status_colours("idle"))

    def _signal_region(self):
        """Market for a signal request, plus whether it came from the last check."""
        current = normalize_id(self.radio_var.get())
        if isinstance(self.last, CheckResult) and self.last.region and self.last.radio_id == current:
            return self.last.region, True
        return ("ca" if self.region_var.get().startswith("Canada") else "com"), False

    def open_signal_page(self):
        """Open SiriusXM's refresh-signal page and copy the Radio ID ready to paste."""
        radio_id = normalize_id(self.radio_var.get())
        region, from_check = self._signal_region()
        url = signal_url(region)
        market = REGION_NAMES.get(region, region)
        lines = [
            f"Refresh signal request for {radio_id or '(no Radio ID entered)'}",
            f"  Market:      {market}" + ("  (from the last check)" if from_check else ""),
            f"  Page:        {url}",
            "",
            "A refresh signal asks SiriusXM to re-broadcast the subscription to the",
            "radio. It does not renew or change the subscription.",
        ]
        if radio_id:
            known = self.last.end_date if (isinstance(self.last, CheckResult)
                                           and self.last.radio_id == radio_id) else None
            self._remember(radio_id, known)
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(radio_id)
                lines += ["", f"Radio ID {radio_id} copied to the clipboard."]
            except Exception:
                pass
        lines += ["", "Enter the Radio ID on the SiriusXM page to complete the request."]
        self._set_status(f"Opening the {market} refresh-signal page ...", *self._status_colours("busy"))
        self._set_detail("\n".join(lines))
        try:
            webbrowser.open(url)
        except Exception:
            self._set_status("Could not open a web browser.", *self._status_colours("error"))

    def _run_async(self, status_text, work, on_error):
        """Run a slow job (network check or browser automation) off the UI thread."""
        if self.busy:
            return
        self.busy = True
        self.check_button.configure(state="disabled")
        self.progress.start(12)
        self._set_status(status_text, *self._status_colours("busy"))
        self._set_detail("")
        threading.Thread(target=self._worker, args=(work, on_error), daemon=True).start()

    def _worker(self, work, on_error):
        try:
            result = work()
        except Exception as exc:  # never let a worker kill the GUI
            result = on_error(exc)
        self.results.put(result)

    def start_check(self):
        if self.busy:
            return
        radio_id = self.radio_var.get().strip()
        if not radio_id:
            self._set_status("Enter a Radio ID first.", *self._status_colours("error"))
            return
        self._remember(radio_id)
        prefer = "CA" if self.region_var.get().startswith("Canada") else "US"
        regions = {"US only": ["com"], "Canada only": ["ca"]}.get(self.region_var.get())
        self._run_async(
            f"Checking {normalize_id(radio_id)} ...",
            lambda: check_subscription(radio_id, prefer=prefer, regions=regions),
            lambda exc: CheckResult(normalize_id(radio_id), False, "error",
                                    error=f"{type(exc).__name__}: {exc}"),
        )

    def _poll(self):
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            self.root.after(120, self._poll)
            return
        self.busy = False
        self.progress.stop()
        self.check_button.configure(state="normal")
        self.last = result
        self._paint(result)
        if result.ok and result.radio_id:
            self._remember_expiry(result.radio_id, result.end_date)
        self.root.after(120, self._poll)

    def _paint(self, result):
        bg, fg = self._status_colours(result.status)
        label = STATUS_TEXT.get(result.status, result.status.upper())
        if result.is_trial and result.status in ("active", "today", "tomorrow"):
            label += "  (trial / demo)"
        self._set_status(label, bg, fg)
        self._set_detail(result.format())


class SettingsDialog:
    """Fonts, result-box size and theme - applied as you pick them.

    There is no OK button: every choice goes straight into the main window and
    into settings.json, so what you see is what you keep. The panel previews the
    chosen text font and the theme's status colours before you go back to the
    results.
    """

    PREVIEW_CHIPS = (("active", "ACTIVE"), ("today", "EXPIRING"), ("overdue", "EXPIRED"),
                     ("no-subscription", "NONE"), ("error", "FAILED"))

    def __init__(self, app):
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.tk = tk
        self.ttk = ttk
        self.controls = {}
        self.themed = []                 # (widget, role) pairs recoloured by refresh()

        self.window = tk.Toplevel(app.root)
        self.window.title("Settings")
        self.window.resizable(False, False)
        self.window.transient(app.root)
        apply_window_icon(self.window)

        self.frame = tk.Frame(self.window, padx=18, pady=16)
        self.frame.pack(fill="both", expand=True)
        self.frame.columnconfigure(1, weight=1)
        self.themed.append((self.frame, "panel"))

        row = self._title(0, "Settings")
        row = self._note(row, "Each change is saved to settings.json straight away.")
        row = self._section(row, "Text font - Radio ID, result box and Recent menu")
        row = self._choice(row, "textFont", "Font", TEXT_FONTS)
        row = self._choice(row, "textSize", "Size", TEXT_SIZES)
        row = self._section(row, "Interface - labels, buttons and headings")
        row = self._choice(row, "interfaceFont", "Font", INTERFACE_FONTS)
        row = self._choice(row, "interfaceSize", "Size", INTERFACE_SIZES)
        row = self._section(row, "Display")
        row = self._box_row(row)
        row = self._section(row, "Appearance - themes colour the banner and the menu too")
        row = self._choice(row, "theme", "Theme",
                           tuple((key, definition["label"])
                                 for key, definition in THEMES.items()))
        row = self._choice(row, "accent", "Accent", tuple(ACCENTS))

        self.preview = tk.Label(self.frame, anchor="w", justify="left", padx=12, pady=8)
        self.preview.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.themed.append((self.preview, "preview"))
        self.chips = tk.Frame(self.frame)
        self.chips.grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.themed.append((self.chips, "panel"))
        self.chip_labels = []
        for key, wording in self.PREVIEW_CHIPS:
            chip = tk.Label(self.chips, text=wording, padx=8, pady=3)
            chip.pack(side="left", padx=(0, 6))
            self.chip_labels.append((key, chip))

        buttons = tk.Frame(self.frame)
        buttons.grid(row=row + 2, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        self.themed.append((buttons, "panel"))
        ttk.Button(buttons, text="Reset to defaults", command=self.reset).pack(side="left")
        ttk.Button(buttons, text="Close", command=self.close).pack(side="right")

        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self._centre()

    # -- building ---------------------------------------------------------

    def _label(self, row, column, text, role, **grid):
        widget = self.tk.Label(self.frame, text=text, anchor="w")
        options = {"padx": (0, 12), "pady": 3}
        options.update(grid)                    # callers may override either padding
        widget.grid(row=row, column=column, sticky="w", **options)
        self.themed.append((widget, role))
        return widget

    def _title(self, row, text):
        self._label(row, 0, text, "title", columnspan=2)
        return row + 1

    def _note(self, row, text):
        self._label(row, 0, text, "muted", columnspan=2, pady=(0, 10))
        return row + 1

    def _section(self, row, text):
        self._label(row, 0, text, "heading", columnspan=2, pady=(10, 2))
        return row + 1

    def _choice(self, row, key, label, options):
        """A labelled drop-down that applies the moment it is picked."""
        self._label(row, 0, label, "text")
        pairs = [(option, option) if not isinstance(option, tuple) else option for option in options]
        box = self.ttk.Combobox(self.frame, state="readonly", width=30,
                                values=[str(shown) for _value, shown in pairs])
        box.grid(row=row, column=1, sticky="ew", pady=3)
        box.bind("<<ComboboxSelected>>", lambda _event: self._changed())
        self.controls[key] = {"widget": box, "pairs": pairs}
        return row + 1

    def _box_row(self, row):
        """The result box size: how many rows and columns of text it shows."""
        self._label(row, 0, "Box size", "text")
        holder = self.tk.Frame(self.frame)
        holder.grid(row=row, column=1, sticky="w", pady=3)
        self.themed.append((holder, "panel"))
        for key, options, unit in (("boxRows", BOX_ROWS, "rows"),
                                   ("boxColumns", BOX_COLUMNS, "characters")):
            box = self.ttk.Combobox(holder, state="readonly", width=5,
                                    values=[str(value) for value in options])
            box.pack(side="left")
            box.bind("<<ComboboxSelected>>", lambda _event: self._changed())
            unit_label = self.tk.Label(holder, text=f" {unit}")
            unit_label.pack(side="left", padx=(4, 14))
            self.themed.append((unit_label, "muted"))
            self.controls[key] = {"widget": box, "pairs": [(value, value) for value in options]}
        return row + 1

    # -- behaviour --------------------------------------------------------

    def _changed(self):
        """Read every control and hand the whole set to the app."""
        chosen = dict(self.app.settings)
        for key, control in self.controls.items():
            shown = control["widget"].get()
            for value, display in control["pairs"]:
                if str(display) == shown:
                    chosen[key] = value
                    break
        if chosen["theme"] != self.app.settings["theme"]:
            chosen["accent"] = THEMES[chosen["theme"]]["accent"]   # a theme brings its own
        self.app.apply_settings(chosen)

    def reset(self):
        """Back to the stock fonts, box size and Light theme."""
        self.app.apply_settings(dict(DEFAULT_SETTINGS))

    def alive(self):
        return bool(self.window.winfo_exists())

    def focus(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self):
        self.window.destroy()
        if self.app.dialog is self:
            self.app.dialog = None

    def _preview_text(self):
        return "\n".join(history_label(entry) for entry in
                         ({"id": "DEMO0001", "endDate": "2099-10-01"},
                          {"id": "SAMPLE03", "endDate": None}))

    def refresh(self):
        """Show the app's current settings and repaint the whole panel."""
        theme, settings = self.app.theme, self.app.settings
        for key, control in self.controls.items():
            for value, display in control["pairs"]:
                if value == settings.get(key):
                    control["widget"].set(str(display))
                    break
        self.window.configure(bg=theme["panel"])
        for widget, role in self.themed:
            if role == "panel":
                widget.configure(bg=theme["panel"])
            elif role == "title":
                widget.configure(bg=theme["panel"], fg=self.app._accent(),
                                 font=self.app._ui_font(4, "bold"))
            elif role == "heading":
                widget.configure(bg=theme["panel"], fg=theme["text_fg"],
                                 font=self.app._ui_font(1, "bold"))
            elif role == "muted":
                widget.configure(bg=theme["panel"], fg=theme["muted"],
                                 font=self.app._ui_font(-1))
            elif role == "text":
                widget.configure(bg=theme["panel"], fg=theme["text_fg"],
                                 font=self.app._ui_font())
            elif role == "preview":
                widget.configure(bg=theme["text_bg"], fg=theme["text_fg"],
                                 font=(settings["textFont"], settings["textSize"]),
                                 text=self._preview_text())
        for key, chip in self.chip_labels:
            background, foreground = theme["status"][key]
            chip.configure(bg=background, fg=foreground, font=self.app._ui_font(-1, "bold"))
        self._centre()

    def _centre(self):
        """Keep the panel over the main window as sizes change."""
        self.window.update_idletasks()
        parent = self.app.root
        left = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.window.winfo_width()) // 2)
        top = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.window.winfo_height()) // 3)
        self.window.geometry(f"+{left}+{top}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        index = argv.index("--selftest")
        out = argv[index + 1] if len(argv) > index + 1 and not argv[index + 1].startswith("-") else None
        return run_selftest(out)

    if "--check" in argv:  # headless mode, mirrors the original CLI
        index = argv.index("--check")
        if len(argv) <= index + 1 or argv[index + 1].startswith("-"):
            print("usage: radioid_checker.py --check <radioID> [--ca] [--verbose]", file=sys.stderr)
            return 2
        prefer = "CA" if "--ca" in argv[index + 2:] else "US"
        result = check_subscription(argv[index + 1], prefer=prefer)
        print(result.format("--verbose" in argv or "-v" in argv))
        return 0 if result.ok else 1

    if "--signal" in argv:  # print the official refresh-signal page (nothing is submitted)
        index = argv.index("--signal")
        rid = argv[index + 1] if len(argv) > index + 1 and not argv[index + 1].startswith("-") else None
        region = "ca" if "--ca" in argv else "com"
        print(signal_url(region))
        if rid:
            print(f"Radio ID: {normalize_id(rid)}")
        return 0

    _enable_windows_dpi()
    import tkinter as tk
    root = tk.Tk()
    CheckerApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
