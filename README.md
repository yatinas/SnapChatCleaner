# Snapchat Web Bulk Snap Cleaner

A Python + Selenium script that bulk-cleans a Snapchat Web conversation:
it scrolls through the chat from bottom to top, and for every **Snap**
(image/video) it finds, it automatically **deletes** it if that option
is available, or **unsaves** it if delete isn't offered. Regular text
messages are left completely untouched.

It attaches to a Chrome browser you already have open (via the Chrome
DevTools debugging port) rather than launching its own — so you log in
and navigate normally, and the script just does the repetitive clicking.

## ⚠️ Before you use this

- This automates clicks inside Snapchat Web. Automated/scripted use of
  a platform like this is commonly restricted by the platform's Terms
  of Service, and using it could get your account flagged, rate-limited,
  or banned. Use at your own risk, on your own account.
- Snapchat's web UI changes over time. This script relies on specific
  CSS class names and SVG icon fingerprints (see "How it works") that
  **will break** whenever Snapchat ships a redesign. Expect to update
  the selectors periodically.
- There is no undo. Deletions and unsaves happen immediately and can't
  be reversed from this tool. Test on a low-stakes conversation first.

## Requirements

- Python 3.8+
- Google Chrome
- [Selenium](https://pypi.org/project/selenium/) (`pip install selenium`)
- A matching `chromedriver` on your PATH (or managed automatically by
  recent Selenium versions)

## Setup

**1. Launch Chrome with remote debugging enabled.**

Close all running Chrome windows first, then start Chrome from a
terminal with a dedicated debugging profile:

macOS:
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/chrome-debug-profile"
```

Windows (PowerShell):
```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:USERPROFILE\chrome-debug-profile"
```

Linux:
```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/chrome-debug-profile"
```

**2. In that Chrome window**, go to `web.snapchat.com`, log in, and open
the specific conversation you want to clean.

**3. Install dependencies and run the script:**

```bash
pip install selenium
python snap_cleaner.py
```

The script locks onto the currently open conversation URL. If you
navigate to a different conversation while it's running, it detects the
change and stops automatically, so it never acts on the wrong chat.

## Usage notes

- Press `Ctrl+C` at any time to stop; it prints a summary of what it did
  (actions taken, scrolls, scans, etc.) before exiting.
- It processes Snaps **bottom to top** so that deleting one doesn't shift
  the position of Snaps still waiting to be processed.
- It only acts on Snaps (media messages) — plain text messages, "you
  deleted a snap" notices, and streak notifications are skipped.

## How it works (for maintainers)

- Connects to the already-running Chrome via
  `debuggerAddress: 127.0.0.1:9222`, so it reuses your logged-in session.
- Finds visible Snap candidates by looking for `<li>` items containing a
  `div.KB4Aq` content block with an `img`/`video` inside, that are
  currently within the viewport.
- Hovers each Snap to reveal its action toolbar (`div.Bhzh6`), then
  identifies the Delete/Unsave buttons by matching known SVG `path d=`
  fingerprints, falling back to `aria-label`/`title`/text matching if
  the icons change.
- Clicks Delete if present, otherwise Unsave, then re-scans (the DOM
  shifts after every deletion).
- Once the current viewport is fully processed with no more actions
  available, it scrolls the conversation pane upward (with overlap, so
  nothing at the scroll boundary gets skipped) and repeats until it
  reaches the top of the chat.

If Snapchat changes their markup, the two things most likely to need
updating are:
- `TRASH_PATH_MARKER` / `UNSAVE_PATH_MARKERS` — the SVG icon fingerprints
- The CSS selectors `div.KB4Aq`, `div.Bhzh6`, `button.NcaQH`

## License

MIT — see [LICENSE](LICENSE).
