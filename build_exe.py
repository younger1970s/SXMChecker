#!/usr/bin/env python3
"""Freeze radioid_checker.py into a single standalone executable.

The result bundles the Python interpreter, tkinter and the requests library, so
the end user does not need Python installed. Run it with::

    python build_exe.py

Output: dist/RadioIDChecker(.exe)
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "RadioIDChecker"
ICON = HERE / "assets" / "sxm_icon.ico"
# The Settings button's gear, in both tones and both sizes - the app picks the
# tone from the theme and the size from the display scaling. Bundled alongside
# the icon so the button is never blank in a frozen build.
GEAR_ICONS = tuple(HERE / "assets" / f"settings_gear_{tone}_{size}.png"
                   for tone in ("dark", "light") for size in (20, 32))


def main():
    entry = HERE / "radioid_checker.py"
    if not entry.exists():
        raise SystemExit(f"entry point not found: {entry}")
    if not ICON.exists():
        raise SystemExit(f"icon not found: {ICON} (run: python assets/make_icon.py)")
    missing = [icon.name for icon in GEAR_ICONS if not icon.exists()]
    if missing:
        raise SystemExit(f"missing Settings artwork: {', '.join(missing)}"
                         " (run: python assets/make_settings_icon.py)")

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is missing. Install build requirements first:")
        print(f"  {sys.executable} -m pip install -r {HERE / 'requirements-build.txt'}")
        return 1

    for stale in (HERE / "build", HERE / "dist"):
        shutil.rmtree(stale, ignore_errors=True)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME,
        # tkinter is a hidden import on some Python builds; name it explicitly.
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        # Window/taskbar icon, and the same file bundled for the Tk window icon.
        "--icon", str(ICON),
        "--add-data", f"{ICON}{os.pathsep}assets",
        # The Settings gear, for the same reason.
        *[argument for gear in GEAR_ICONS
          for argument in ("--add-data", f"{gear}{os.pathsep}assets")],
        str(entry),
    ]
    print("Building:", " ".join(command))
    result = subprocess.run(command, cwd=HERE)
    if result.returncode != 0:
        return result.returncode

    target = HERE / "dist" / (NAME + (".exe" if sys.platform == "win32" else ""))
    print(f"\nBuilt: {target}" if target.exists() else "\nBuild finished but output was not found.")
    return 0 if target.exists() else 1


if __name__ == "__main__":
    raise SystemExit(main())
