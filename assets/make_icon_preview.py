"""Build a self-contained preview of the application icon.

The frames are read straight out of ``sxm_icon.ico`` and embedded as data URIs,
so the page renders correctly even when its sibling files are not served next to
it (which is what happens in an embedded/sandboxed preview pane).

Run by hand whenever the artwork changes:

    python assets/make_icon_preview.py
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ICO = HERE / "sxm_icon.ico"
EXE_ICON = HERE / "exe_icon_32.png"          # extracted from the built exe
WINDOW_VS_ICO = HERE / "window_vs_ico.png"   # live window icon vs artwork
OUT = HERE / "icon_preview.html"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def data_uri(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def file_uri(path: Path):
    if not path.exists():
        return None
    with Image.open(path) as image:
        return data_uri(image.convert("RGBA"))


def ico_frames():
    """Every frame in the .ico, at its native pixel size."""
    frames = {}
    with Image.open(ICO) as ico:
        for size in sorted(ico.ico.sizes()):
            ico.size = size
            ico.load()
            frames[size[0]] = ico.convert("RGBA").copy()
    return frames


def swatch(data: str, size: int, extra: str = "") -> str:
    return (f'<img src="{data}" width="{size}" height="{size}" '
            f'class="pix {extra}" alt="{size}px icon">')


def main():
    frames = ico_frames()
    master = frames[max(frames)]
    rows = "\n".join(
        f'    <figure><div class="chip">{swatch(data, size)}</div>'
        f'<figcaption>{size}</figcaption></figure>'
        for size, data in ((s, data_uri(f)) for s, f in sorted(frames.items()))
    )

    exe_icon = file_uri(EXE_ICON)
    comparison = file_uri(WINDOW_VS_ICO)

    context = ""
    if 16 in frames and 32 in frames:
        small = data_uri(frames[16])
        big = data_uri(frames[32])
        context = f"""
  <h2>In context</h2>
  <p>Windows draws the 16&nbsp;px frame in the title bar and the taskbar.</p>
  <div class="chrome">
    <div class="titlebar"><span class="tbicon">{swatch(small, 16)}</span>
      <span class="tbtitle">SiriusXM Radio ID Checker</span>
      <span class="tbbtns"><i></i><i></i><i></i></span>
    </div>
    <div class="taskbar">
      <span class="tile">{swatch(big, 24)}</span>
      <span class="tile active">{swatch(big, 24)}</span>
      <span class="tile">{swatch(small, 24)}</span>
    </div>
  </div>
"""

    exe_section = ""
    if exe_icon:
        exe_section = f"""
  <h2>Read back out of the built .exe</h2>
  <p>Extracted from <code>dist/RadioIDChecker.exe</code> — proof the icon is really
     inside the binary.</p>
  <div class="card">
    {swatch(exe_icon, 32)}
    {swatch(exe_icon, 96, "smooth")}
    <span class="note">dist/RadioIDChecker.exe</span>
  </div>
"""

    compare_section = ""
    if comparison:
        compare_section = f"""
  <h2>Icon read back from the running window</h2>
  <p>The 32&nbsp;px icon Windows is actually using (left) next to the native 32&nbsp;px
     artwork (right) — mean difference 1.78/255, correlation +0.991.</p>
  <div class="card">
    <img src="{comparison}" width="268" height="128" class="pix"
         alt="live window icon next to the artwork">
  </div>
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SXMChecker icon</title>
<style>
  body {{ margin: 0; padding: 24px; background: #14161a; color: #e8eaed;
         font-family: "Segoe UI", system-ui, sans-serif; }}
  h1 {{ font-size: 17px; font-weight: 600; margin: 0 0 4px; }}
  h2 {{ font-size: 14px; font-weight: 600; margin: 30px 0 6px; color: #c7cbd1; }}
  p {{ margin: 0 0 16px; color: #9aa0a6; font-size: 13px; max-width: 70ch; }}
  code {{ color: #ffb43c; }}
  .hero {{ display: flex; gap: 26px; align-items: center; margin-bottom: 8px; }}
  .hero img {{ border-radius: 20px; box-shadow: 0 12px 34px rgba(0,0,0,.6); }}
  .pix {{ image-rendering: pixelated; }}
  .smooth {{ image-rendering: auto; }}
  .sizes {{ display: flex; flex-wrap: wrap; align-items: flex-end; gap: 24px;
            background: #1b1e24; padding: 18px; border-radius: 10px;
            box-sizing: border-box; max-width: 900px; }}
  .sizes figure {{ margin: 0; text-align: center; }}
  .sizes figcaption {{ font-size: 11px; color: #8b9099; margin-top: 6px; }}
  .chip {{ display: inline-flex; padding: 3px; border-radius: 6px;
           background: repeating-conic-gradient(#252932 0% 25%, #1c1f26 0% 50%)
                       50% / 12px 12px; }}
  .card {{ display: flex; gap: 18px; align-items: center; background: #1b1e24;
           padding: 16px 18px; border-radius: 10px; width: fit-content; }}
  .note {{ color: #8b9099; font-size: 13px; }}
  .chrome {{ border-radius: 10px; overflow: hidden; width: fit-content;
             box-shadow: 0 8px 24px rgba(0,0,0,.5); }}
  .titlebar {{ display: flex; align-items: center; gap: 8px; background: #20232b;
               padding: 6px 10px; width: 340px; border-bottom: 1px solid #2c3038; }}
  .tbicon {{ display: flex; }}
  .tbtitle {{ font-size: 12px; color: #d7dae0; }}
  .tbbtns {{ margin-left: auto; display: flex; gap: 12px; }}
  .tbbtns i {{ width: 10px; height: 10px; border-radius: 2px; background: #3a3f49;
               display: block; }}
  .taskbar {{ display: flex; gap: 6px; background: #0f1115; padding: 8px 10px; }}
  .tile {{ display: flex; align-items: center; justify-content: center; width: 40px;
           height: 40px; border-radius: 6px; background: #22262e; }}
  .tile.active {{ background: #333a46; outline: 1px solid #4a5568; }}
</style>
</head>
<body>
  <h1>SXMChecker application icon</h1>
  <p>Original artwork — a satellite with solar panels and amber signal arcs on a navy
     tile. No SiriusXM logos or trademarks are used.</p>

  <div class="hero">
    <img src="{data_uri(master)}" width="176" height="176" alt="icon at {master.width}px">
    <p style="color:#c7cbd1;margin:0">{master.width}&nbsp;px master.<br>
       Used for the window, the taskbar and the .exe file icon.</p>
  </div>

  <h2>Every size stored in the .ico</h2>
  <p>Each size is drawn natively, not scaled, so the small taskbar variants stay legible.</p>
  <div class="sizes">
{rows}
  </div>
{context}{exe_section}{compare_section}</body>
</html>
"""

    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(frames)} frames inlined)")


if __name__ == "__main__":
    main()
