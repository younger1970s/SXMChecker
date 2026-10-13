# SiriusXM Radio ID Checker

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
code is `0` on success, `1` on a failed check, `2` on bad usage.

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
| `screenshot.png` / `preview.html` | Capture of the running GUI. |
| `assets/make_gui_preview.py` | Rebuilds `preview.html` from `screenshot.png`, inlining the image. |

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
