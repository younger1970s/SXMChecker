# SiriusXM Radio ID Checker for Windows

A small standalone GUI that does two things with a Radio ID:

1. **Check Subscription** — does this Radio ID currently have a subscription
   running? It reports the plan, whether it is a trial/demo or a paid plan, the
   account status, and the start/end dates.
2. **Request Signal View Web** — when a radio has lost its signal, it opens
   SiriusXM's official refresh page in your browser and puts the Radio ID on the
   clipboard, ready to paste into the form.

It extracts the subscription check from the companion dealer CLI (`SxmClient.check`),
wraps it in a Tkinter GUI, and packages it so end users **do not need Python
installed**.

> **Not included on purpose:** renewal and trial activation. Enabling a
> non-active Radio ID (adding or extending a trial/subscription) sends a dealer
> request that changes an account, so it is deliberately **not** part of this
> app. The only "signal" action here is a request for SiriusXM to re-broadcast
> the existing subscription to the radio.

## How the check works

The check posts the Radio ID to SiriusXM's care/registration endpoint:

```
POST https://care.siriusxm.com/services/account/registration/non-pii   {"radioId": "<id>"}
POST https://care.siriusxm.ca/services/account/registration/non-pii    {"radioId": "<id>"}
```

Both markets are called. By default the **United States** endpoint is tried
first and **Canada** is the fallback — this is the CLI's default order, and the
`--ca`/“Canada first” option flips it. The plan with the latest valid end date
wins; a `DEMO` plan type (or `hasDemoSubscription`) marks a trial, and the end
date decides active vs. expired.

Radio IDs are 8 characters or 12 digits (17-character inputs are VINs).

## Request Signal View Web (refresh a radio that lost its signal)

**Request Signal View Web** opens the official page and copies the Radio ID, so
you can paste it into the form and submit it yourself:

| Market | Page |
|--------|------|
| United States | <https://www.siriusxm.com/refresh> |
| Canada | <https://www.siriusxm.ca/refresh> |

The market comes from the last check when it matches the Radio ID you typed,
otherwise from the **Market order** box (United States unless you pick a Canada
option). The button displays the page URL and the Radio ID it copied; paste that
into the `Radio ID, VIN, or Phone Number` field and press **REQUEST SIGNAL** on
the page.

The app deliberately drives no browser of its own. Earlier versions automated
this form with Selenium, which required Google Chrome plus a downloaded
ChromeDriver and broke whenever SiriusXM changed its markup. Opening the real
page by hand removes that whole dependency.

**Why the web page instead of the dealer signal API?** The API that the Android
app uses for "Request Signal" authenticates with the SiriusXM dealer **app key
and secret**. This tool ships no credentials at all, so it uses SiriusXM's own
public page instead. Nothing here can renew or change a subscription.

## Recent Radio IDs (one click to re-check)

**Recent** in the form row remembers the IDs that have been through the app, so
you do not have to retype one. The Radio ID field is only as wide as the longest
thing it accepts (a 17-character VIN), which leaves Recent the room to be a long
box: it takes any leftover width and grows as the window does.

- An ID is remembered when you press **Check Subscription** or **Request Signal
  View Web**.
- The menu lists them **newest first**; click one to drop it straight into the
  Radio ID field (it is not checked on its own — press the button when ready).
- Each entry carries the **expiry date the last check found**, so the menu reads
  `DEMO0001 - expires 2099-10-01`. Until a check has produced a date it reads
  `SAMPLE03 - Unknown`. The menu is monospaced and pads the ID to a VIN's width,
  so the dates line up in one column however long the IDs are.
- Reusing an ID moves it back to the top, duplicates collapse, and the list stops
  at **15** entries, dropping the oldest.
- **Clear history** at the bottom of the menu empties the list.

It is stored per user, so every Windows account keeps its own list:

```
%APPDATA%\SXMChecker\history.json
```

The expiry sits next to each ID so the menu can show it without checking again:

```json
{
  "radioIds": [
    { "id": "DEMO0001", "endDate": "2099-10-01" },
    { "id": "SAMPLE03", "endDate": "Unknown" }
  ]
}
```

A completed check replaces the stored date; a check that **failed** leaves it
alone, because nothing was learned, and a radio that comes back with no
subscription is stored as `Unknown` again. So an entry only ever goes stale
because a check succeeded.

The write is atomic (a temporary file is renamed over the old one), a missing or
corrupted file is treated as an empty list rather than an error, and anything
that is not shaped like a Radio ID / VIN is ignored. A file written by an earlier
build — plain IDs with no dates — still loads, with `Unknown` expiry, and so does
one saved with a byte-order mark. On
Linux/macOS the folder falls back to `$XDG_DATA_HOME/SXMChecker` or
`~/.sxm_checker`. The list is a convenience only and holds no credentials; the
command line (`--check`, `--signal`) never touches it.

## Settings (fonts, theme, box size)

**Settings** (with its gear icon) opens a panel that changes how the window
looks. There is no OK button: every choice is applied as you pick it and written
straight to disk, so what you see is what you keep. **Reset to defaults** puts
everything back.

| Setting | What it changes |
|---------|-----------------|
| **Check result font / size** | The Radio ID field, the result box and the **Recent** menu. The monospaced faces (Consolas, Cascadia Mono, Cascadia Code, Lucida Console, Courier New, Courier) keep the result columns aligned; proportional faces are offered too. Text sizes run 8-20. |
| **Interface font / size** | The labels, buttons and headings (Segoe UI, Tahoma, Verdana, Calibri, Trebuchet MS, Georgia, Consolas, Arial; sizes 8-16). |
| **Box size** | How many rows (4-30) and characters (40-160) the result box keeps, so the window can be sized for the output you expect. |
| **Theme** | Light, Dark, Midnight, Slate, High contrast and Amber CRT. |
| **Accent** | Blue, Teal, Violet, Amber or Rose - tints the heading, the progress bar and the focus ring. Picking a theme adopts that theme's accent. |

The panel previews the chosen text font over the theme's own status colours, so
the look can be judged before you go back to the results.

Each theme carries a whole palette, including the **status banner**: the pastel
green that reads "ACTIVE" on white is unreadable on black, so every theme
defines its own background/foreground pair for every status (active, expiring
today, expired, no subscription, failed, busy, idle). `--selftest` asserts at
least 4.0:1 contrast for each pair and 4.5:1 for body text.

The dark themes switch ttk to **clam**, because Windows' native "vista" look
cannot be recoloured; on Light the Recent menu is handed back to the system
colours, so it matches Explorer's own menus.

Preferences live beside the recent list:

```
%APPDATA%\SXMChecker\settings.json
```

```json
{
  "textFont": "Consolas",
  "textSize": 10,
  "interfaceFont": "Segoe UI",
  "interfaceSize": 9,
  "boxRows": 8,
  "boxColumns": 80,
  "theme": "light",
  "accent": "Blue"
}
```

The write is atomic, and every value is validated on the way in: an unknown
font, theme or accent falls back to the default, an out-of-range size is clamped
to the nearest offered one, numbers given as text still work, and unknown keys
are dropped. A hand-edited or half-written file therefore cannot leave a control
unusable. Files saved with a byte-order mark (Windows PowerShell, older
Notepad) load too. Deleting the file restores the defaults.

## Run from source

```sh
python -m pip install -r requirements.txt
python radioid_checker.py            # launches the GUI
```

Required: Python 3.10+ (Tkinter ships with the standard python.org installer) and
`requests`. No browser automation, no Google Chrome, no driver downloads.

### Command line (optional)

```sh
python radioid_checker.py --check <radioID>              # print the status
python radioid_checker.py --check <radioID> --ca --verbose
python radioid_checker.py --signal <radioID> [--ca]      # print the refresh page + ID
python radioid_checker.py --selftest                     # offline check of the logic
```

`--signal` only prints the official page for the market and the normalised Radio
ID — nothing is opened or submitted, so it is safe to call from a script. Exit
code is `0` on success, `1` on a failed check, `2` on bad usage. These headless
modes do not read or write the Recent list.

## Build the standalone .exe (no Python needed by end users)

```sh
python build_exe.py         # or just double-click build_exe.bat
```

This uses PyInstaller to bundle the interpreter, Tkinter and `requests` into a
single file:

```
dist/RadioIDChecker.exe     (~17 MB, one file, nothing to install)
```

Hand that `.exe` to an end user — they double-click it and the GUI opens. As long
as they have a web browser, **Request Signal View Web** can open the SiriusXM
page; nothing else has to be installed.

To verify a build without using the network or a browser:

```sh
dist/RadioIDChecker.exe --selftest        # prints PASS/FAIL, exit 0 = ok
```

The build script installs its own requirements first; for a manual install use
`requirements-build.txt`.

## Files

| File | Purpose |
|------|---------|
| `radioid_checker.py` | The GUI app and the extracted check logic (`check_subscription`). |
| `build_exe.py` / `build_exe.bat` | Freeze the app into a single executable. |
| `requirements.txt` | Runtime dependencies (`requests`). |
| `requirements-build.txt` | Build-time dependency (adds `pyinstaller`). |
| `dist/RadioIDChecker.exe` | The built, ready-to-distribute executable. |
| `assets/sxm_icon.ico` | Application icon used for the window, taskbar and the `.exe` itself. |
| `assets/make_icon.py` | Regenerates the icon artwork (needs Pillow; run by hand). |
| `assets/make_icon_preview.py` | Builds `icon_preview.html` showing every embedded size and how Windows draws them. |
| `assets/settings_gear_*.png` | The Settings gear in a dark and a light tone, at 20 px and 32 px. |
| `assets/make_settings_icon.py` | Regenerates the gear artwork (needs Pillow; run by hand). |
| `screenshot.png` / `screenshot-settings.png` / `preview.html` | Captures of the running GUI - as it starts, and with the Settings panel open on a dark theme. |
| `assets/make_gui_preview.py` | Rebuilds `preview.html` from the captures, inlining the images and reading the theme names from the app. |

## Icon

The app uses an original mark — a satellite with signal arcs on a navy tile —
so it no longer shows the default Tk feather. No SiriusXM logos or trademarks are
used. It is applied in three places:

- the `.exe` file icon, via `--icon` in [build_exe.py](build_exe.py)
- the **title bar / taskbar / Alt+Tab**, by sending the `.ico`'s own 32 px and
  16 px frames to the window with `WM_SETICON` (`_set_native_window_icons`)
- the **dialogs**, via `root.iconbitmap(default=…)`, which installs the icon on
  the window class so message boxes inherit it

The explicit `WM_SETICON` step exists because Tk's class icon left Windows
scaling the 16 px frame up to the 32 px slot, which looked soft. Sending an
exactly sized frame keeps it sharp.

`assets/sxm_icon.ico` holds seven natively rendered sizes (16-256 px) so the small
taskbar variants stay legible. To change the artwork, edit
[assets/make_icon.py](assets/make_icon.py) and run:

```sh
python -m pip install pillow
python assets/make_icon.py     # rewrites sxm_icon.ico and sxm_icon.png
```

The generated `.ico` is committed, so normal builds never need Pillow.

To look at the result, build the preview page and open it:

```sh
python assets/make_icon_preview.py    # writes assets/icon_preview.html
```

Every image on that page is embedded as a data URI, so it renders on its own —
opening it by hand or through a preview pane that does not serve sibling files
both work, with no broken images.

### Settings gear

The gear on the **Settings** button is generated by
[assets/make_settings_icon.py](assets/make_settings_icon.py) in two tones — a dark
glyph for the light theme and a light one for the dark themes — at 20 px and
32 px, and bundled by [build_exe.py](build_exe.py). The button swaps tone with the
theme, and falls back to plain text if the artwork is missing, so a build without
the PNGs still works.

```sh
python -m pip install pillow
python assets/make_settings_icon.py    # rewrites the four gear PNGs
```

## Notes & limitations

- **No credentials in this code.** There are no app keys, secrets or tokens in
  the script or the built executable; the signal goes through SiriusXM's public
  web page, so none are needed. (`--selftest` also asserts no credential-looking
  literals are present in the source.)
- **No browser automation.** The page is opened with `webbrowser.open` in the
  default browser; the app never drives a browser itself, so there is no
  Chrome/ChromeDriver requirement and nothing that breaks when SiriusXM changes
  its page markup. `--selftest` fails if such a dependency is ever reintroduced.
- The check depends on SiriusXM's care endpoint returning the same shape; if it
  changes, `--selftest` still passes (it uses fixed fixtures).
- macOS/Linux builds: `python build_exe.py` produces a binary for the current
  OS only. Build on Windows for a Windows `.exe`.
- The window icon depends on `assets/sxm_icon.ico` being bundled; `--selftest`
  checks that it resolved **and** that the file really contains native 16 px and
  32 px frames, so a broken build or a flattened icon is caught immediately.
- **The Recent list is kept per Windows user**, in
  `%APPDATA%\SXMChecker\history.json` (up to 15 IDs, each with its expiry or
  `Unknown`). It holds Radio IDs and expiry dates only — no credentials, no
  account details — and **Clear history** empties it. Delete that file if you
  would rather leave no trace at all.
- **The settings are per Windows user** as well, in
  `%APPDATA%\SXMChecker\settings.json` — fonts, box size, theme and accent only,
  no credentials. Delete it, or press **Reset to defaults**, to go back to stock.
- **Colour choices are yours to make.** The palettes are checked for contrast, not
  for taste; if a theme reads badly on your display, the accent and box size are
  there to tune it, and the file above is plain JSON.
