"""Build the GUI preview page from ``screenshot.png`` (and the Settings capture).

The captures are embedded as data URIs so the page renders on its own, even when
its sibling files are not served next to it (which is what happens in an
embedded/sandboxed preview pane). The theme names are read from the app itself,
so the page cannot drift from what ships.

Take fresh captures of the running app first, then run this by hand:

    python assets/make_gui_preview.py
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHOT = ROOT / "screenshot.png"
SETTINGS_SHOT = ROOT / "screenshot-settings.png"
OUT = ROOT / "preview.html"


def inline(path):
    """Return ``(data_uri, width, height)`` for an image file."""
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="PNG")
        width, height = rgb.size
    data = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    return data, width, height


def card(data, width, height, alt):
    return (
        f'  <div class="card"><img src="{data}" width="{width}" height="{height}"\n'
        f'       alt="{alt}"></div>'
    )


def theme_names():
    """The shipped theme labels, taken from the app so they cannot drift apart."""
    sys.path.insert(0, str(ROOT))
    try:
        import radioid_checker
    finally:
        sys.path.pop(0)
    return [definition["label"] for definition in radioid_checker.THEMES.values()]


def settings_section():
    """The Settings panel: its capture (if taken) and what each control does."""
    names = theme_names()
    if len(names) > 1:
        listed = ", ".join(names[:-1]) + " and " + names[-1]
    else:
        listed = names[0] if names else "several"

    if SETTINGS_SHOT.exists():
        data, width, height = inline(SETTINGS_SHOT)
        shot = "\n\n" + card(data, width, height, "settings panel open on a dark theme")
    else:
        shot = ""

    return f"""  <h2>Settings</h2>
  <p>The <b>Settings</b> button opens a panel that changes the look of the window
     while you use it: every choice is applied as you pick it, and remembered in
     <code>%APPDATA%\\SXMChecker\\settings.json</code>, so there is no OK button.
     <b>Reset to defaults</b> puts everything back.</p>{shot}

  <ul>
    <li><b>Check result font / size</b> - the Radio ID field, the result box and
        the <b>Recent</b> menu. Monospaced faces (Consolas, Cascadia Mono, Lucida
        Console, Courier New) keep the result columns aligned; the proportional
        faces are there if you prefer them.</li>
    <li><b>Interface font / size</b> - the labels, buttons and headings.</li>
    <li><b>Box size</b> - how many rows and characters the result box keeps, so the
        window can be sized for the output you expect.</li>
    <li><b>Theme</b> - {listed}. Each theme carries its own status colours, so the
        banner stays readable on black as well as on white.</li>
    <li><b>Accent</b> - tints the heading, the progress bar and the focus ring.
        Picking a theme adopts that theme's accent.</li>
  </ul>"""


def main():
    data, width, height = inline(SHOT)

    OUT.write_text(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SiriusXM Radio ID Checker - GUI</title>
<style>
  body {{ margin: 0; padding: 24px; background: #14161a; color: #e8eaed;
         font-family: "Segoe UI", system-ui, sans-serif; }}
  h1 {{ font-size: 17px; font-weight: 600; margin: 0 0 4px; }}
  h2 {{ font-size: 14px; font-weight: 600; margin: 26px 0 6px; color: #c7cbd1; }}
  p, li {{ color: #9aa0a6; font-size: 13px; line-height: 1.55; max-width: 78ch; }}
  img {{ display: block; max-width: 100%; height: auto; border-radius: 10px;
         box-shadow: 0 12px 34px rgba(0,0,0,.6); }}
  code {{ color: #ffb43c; }}
  pre {{ display: inline-block; margin: 0; padding: 12px 16px; border-radius: 8px;
         background: #1b1e24; color: #ffb43c; font-size: 13px; line-height: 1.6; }}
  .card {{ display: inline-block; max-width: 100%; box-sizing: border-box;
           background: #1b1e24; padding: 18px; border-radius: 12px; }}
</style>
</head>
<body>
  <h1>SiriusXM Radio ID Checker</h1>
  <p>Captures of the running app - the same window
     <code>dist/RadioIDChecker.exe</code> opens, first as it starts, then with the
     Settings panel open on a dark theme. The result on screen is sample data
     (<code>DEMO0001</code> is fictional): a live check needs a real Radio ID.</p>

{card(data, width, height, "application window")}

  <h2>Buttons</h2>
  <ul>
    <li><b>Check Subscription</b> - looks the Radio ID up on the US/Canada care
        endpoints and reports the plan, the trial/demo flag, the dates and the
        days left.</li>
    <li><b>Request Signal View Web</b> - opens SiriusXM's official refresh page in
        your browser and copies the Radio ID to the clipboard, ready to paste into
        the form. The app drives no browser of its own.</li>
    <li><b>Settings</b> - opens the panel described below; the gear next to it
        follows the theme's accent.</li>
    <li><b>Clear</b> / <b>Copy result</b> - reset the form, or copy the shown
        result to the clipboard.</li>
  </ul>

{settings_section()}

  <h2>Recent</h2>
  <p><b>Recent</b> lists the Radio IDs you have checked or requested a signal
     for, newest first, each with the <b>expiry date the last check found</b>;
     pick one to drop it straight into the field. The list is remembered between
     runs in <code>%APPDATA%\\SXMChecker\\history.json</code> (up to 15 IDs), and
     <b>Clear history</b> at the bottom of the menu removes it - including the
     file's contents.</p>
  <p>The Radio ID field is sized for a VIN (17 characters, the longest ID
     accepted), and <b>Recent</b> takes the width that frees up. The menu itself
     is monospaced and pads the ID to that same width, so the expiries line up in
     one column; until a check has produced a date the entry reads
     <code>Unknown</code>:</p>
  <pre>DEMO0001          - expires 2099-10-01
TEST0002          - expires 2027-06-30
SAMPLE03          - Unknown</pre>

  <h2>Market order</h2>
  <p><b>Auto (US, then Canada)</b> is the default and matches the dealer CLI;
     <b>Canada first</b> flips the order, and <b>US only</b> / <b>Canada only</b>
     pin a single market. The choice also decides which refresh page
     <b>Request Signal View Web</b> opens.</p>
</body>
</html>
""", encoding="utf-8")
    extra = " + settings capture" if SETTINGS_SHOT.exists() else ""
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {width}x{height}{extra} inlined)")


if __name__ == "__main__":
    main()
