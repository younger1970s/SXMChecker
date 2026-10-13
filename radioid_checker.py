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

Only the Python standard library plus ``requests`` is required. ``build_exe.py``
freezes this into a single .exe so end users do not need Python installed.
"""
from __future__ import annotations

import json
import queue
import re
import sys
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
        self.root = root
        self.results = queue.Queue()
        self.last = None
        self.busy = False

        root.title("SiriusXM Radio ID Checker")
        root.minsize(560, 470)
        apply_window_icon(root)          # window title bar, taskbar and dialogs
        try:
            ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
        except Exception:
            pass

        main = ttk.Frame(root, padding=14)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        header = ttk.Label(main, text="SiriusXM Radio ID Checker",
                           font=("Segoe UI", 15, "bold"))
        header.grid(row=0, column=0, sticky="w")
        ttk.Label(main, text="Check a subscription and request a refresh signal (US + Canada).",
                  foreground="#555").grid(row=1, column=0, sticky="w", pady=(2, 10))

        form = ttk.Frame(main)
        form.grid(row=2, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Radio ID:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.radio_var = tk.StringVar()
        self.radio_entry = ttk.Entry(form, textvariable=self.radio_var, font=("Consolas", 12))
        self.radio_entry.grid(row=0, column=1, sticky="ew")
        self.radio_entry.bind("<Return>", lambda _e: self.start_check())
        self.radio_entry.bind("<KeyRelease>", self._uppercase)

        ttk.Label(form, text="Market order:").grid(row=0, column=2, sticky="w", padx=(12, 8))
        self.region_var = tk.StringVar(value="Auto (US, then Canada)")
        self.region_box = ttk.Combobox(form, textvariable=self.region_var, state="readonly", width=22,
                                       values=("Auto (US, then Canada)", "Canada first", "US only", "Canada only"))
        self.region_box.grid(row=0, column=3, sticky="w")

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

        banner = ttk.Frame(main)
        banner.grid(row=4, column=0, sticky="nsew")
        banner.columnconfigure(0, weight=1)
        banner.rowconfigure(1, weight=1)
        self.status_label = tk.Label(banner, text="Enter a Radio ID and press Check.",
                                     font=("Segoe UI", 13, "bold"), anchor="w",
                                     bg="#eeeeee", fg="#333333", padx=12, pady=10)
        self.status_label.grid(row=0, column=0, sticky="ew")

        self.detail = tk.Text(banner, height=8, wrap="word", font=("Consolas", 10),
                              relief="solid", borderwidth=1)
        self.detail.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.detail.configure(state="disabled")

        footer = ttk.Frame(main)
        footer.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(footer, text="check + signal request only - never renews or changes a subscription",
                  foreground="#777").pack(side="left")

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

    def _set_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def clear(self):
        self.radio_var.set("")
        self.last = None
        self._set_status("Enter a Radio ID and press Check.", "#eeeeee", "#333333")
        self._set_detail("")
        self.radio_entry.focus_set()

    def copy_result(self):
        if not self.last:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last.format())
        self._set_status("Result copied to clipboard.", "#eeeeee", "#333333")

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
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(radio_id)
                lines += ["", f"Radio ID {radio_id} copied to the clipboard."]
            except Exception:
                pass
        lines += ["", "Enter the Radio ID on the SiriusXM page to complete the request."]
        self._set_status(f"Opening the {market} refresh-signal page ...", "#e8f0fe", "#1a3b8b")
        self._set_detail("\n".join(lines))
        try:
            webbrowser.open(url)
        except Exception:
            self._set_status("Could not open a web browser.", "#fce8e6", "#c5221f")

    def _run_async(self, status_text, work, on_error):
        """Run a slow job (network check or browser automation) off the UI thread."""
        if self.busy:
            return
        self.busy = True
        self.check_button.configure(state="disabled")
        self.progress.start(12)
        self._set_status(status_text, "#e8f0fe", "#1a3b8b")
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
            self._set_status("Enter a Radio ID first.", "#fdecea", "#b3261e")
            return
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
        self.root.after(120, self._poll)

    def _paint(self, result):
        palette = {
            "active": ("#e6f4ea", "#137333", "ACTIVE - subscription is running"),
            "today": ("#fef7e0", "#a06a00", "ACTIVE - expires TODAY"),
            "tomorrow": ("#fef7e0", "#a06a00", "ACTIVE - expires TOMORROW"),
            "overdue": ("#fce8e6", "#c5221f", "EXPIRED - subscription has ended"),
            "no-subscription": ("#f1f3f4", "#5f6368", "NO SUBSCRIPTION - nothing on file"),
            "error": ("#fce8e6", "#c5221f", "CHECK FAILED"),
            "dates unavailable": ("#fef7e0", "#a06a00", "ACTIVE? - dates unavailable"),
        }
        bg, fg, label = palette.get(result.status, ("#f1f3f4", "#5f6368", result.status.upper()))
        if result.is_trial and result.status in ("active", "today", "tomorrow"):
            label += "  (trial / demo)"
        self._set_status(label, bg, fg)
        self._set_detail(result.format())


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
