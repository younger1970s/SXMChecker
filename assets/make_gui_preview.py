"""Build the GUI preview page from ``screenshot.png``.

The capture is embedded as a data URI so the page renders on its own, even when
its sibling files are not served next to it (which is what happens in an
embedded/sandboxed preview pane).

Take a fresh capture of the running app first, then run this by hand:

    python assets/make_gui_preview.py
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHOT = ROOT / "screenshot.png"
OUT = ROOT / "preview.html"


def main():
    with Image.open(SHOT) as image:
        rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="PNG")
        data = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        width, height = rgb.size

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
  <p>Capture of the running app - the same window
     <code>dist/RadioIDChecker.exe</code> opens.</p>

  <div class="card"><img src="{data}" width="{width}" height="{height}"
       alt="application window"></div>

  <h2>Buttons</h2>
  <ul>
    <li><b>Check Subscription</b> - looks the Radio ID up on the US/Canada care
        endpoints and reports the plan, the trial/demo flag, the dates and the
        days left.</li>
    <li><b>Request Signal View Web</b> - opens SiriusXM's official refresh page in
        your browser and copies the Radio ID to the clipboard, ready to paste into
        the form. The app drives no browser of its own.</li>
    <li><b>Clear</b> / <b>Copy result</b> - reset the form, or copy the shown
        result to the clipboard.</li>
  </ul>

  <h2>Recent</h2>
  <p><b>Recent</b> lists the Radio IDs you have checked or requested a signal
     for, newest first, each with the <b>expiry date the last check found</b>;
     pick one to drop it straight into the field. The list is remembered between
     runs in <code>%APPDATA%\SXMChecker\history.json</code> (up to 15 IDs), and
     <b>Clear history</b> at the bottom of the menu removes it - including the
     file's contents.</p>
  <p>Until a check has produced a date the entry reads <code>Unknown</code>, so
     the menu looks like this:</p>
  <pre>DEMO0001 - expires 2099-10-01
TEST0002 - expires 2027-06-30
SAMPLE03 - Unknown</pre>

  <h2>Market order</h2>
  <p><b>Auto (US, then Canada)</b> is the default and matches the dealer CLI;
     <b>Canada first</b> flips the order, and <b>US only</b> / <b>Canada only</b>
     pin a single market. The choice also decides which refresh page
     <b>Request Signal View Web</b> opens.</p>
</body>
</html>
""", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {width}x{height} capture inlined)")


if __name__ == "__main__":
    main()
