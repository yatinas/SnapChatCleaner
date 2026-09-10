"""
Snapchat Web Bulk Snap Cleaner
===============================
Scans an open Snapchat Web conversation from bottom to top and,
for each Snap it finds, automatically clicks "Delete" if available,
otherwise "Unsave". Regular text messages are left untouched.

Requires Chrome running with remote debugging enabled and
Snapchat Web already open in a conversation. See README.md.
"""

import time
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException


# ============================================================
# CONFIGURATION
# ============================================================

DEBUGGER_ADDRESS = "127.0.0.1:9222"

ACTION_POLL_INTERVAL = 0.02   # how often to re-check for a hover action
ACTION_TIMEOUT = 0.16         # max time to wait for hover action to appear
POST_ACTION_WAIT = 0.05       # pause after clicking an action
POST_SCROLL_WAIT = 0.06       # pause after scrolling
SCROLL_RATIO = 0.88           # overlap ratio so no Snap is skipped at edges

TRASH_PATH_MARKER = "M9.75 4.624"       # SVG path fingerprint for "Delete"
UNSAVE_PATH_MARKERS = ("M18.742 4.576",)  # SVG path fingerprint(s) for "Unsave"

MAX_NO_ACTION_ATTEMPTS = 2    # times to retry a Snap with no action before skipping


# ============================================================
# CONNECT TO EXISTING CHROME
# ============================================================

def connect_to_chrome():
    options = Options()
    options.add_experimental_option("debuggerAddress", DEBUGGER_ADDRESS)

    print("Connecting to Chrome...")
    driver = webdriver.Chrome(options=options)
    print("Connected successfully!")
    print("Current URL:", driver.current_url)

    return driver


# ============================================================
# CONVERSATION SAFETY
# ============================================================

def get_locked_url(driver):
    url = driver.current_url
    parsed = urlparse(url)

    if parsed.netloc != "www.snapchat.com":
        raise RuntimeError("Snapchat Web is not open.")

    if not parsed.path.startswith("/web/"):
        raise RuntimeError("Open the Snapchat conversation first.")

    return url


def conversation_is_locked(driver, locked_url):
    return driver.current_url == locked_url


def get_conversation_pane(driver, locked_url):
    parts = [p for p in urlparse(locked_url).path.split("/") if p]

    if len(parts) < 2:
        return None

    conversation_id = parts[1]

    try:
        return driver.find_element(By.ID, f"cv-{conversation_id}")
    except Exception:
        return None


# ============================================================
# MESSAGE CONTENT
# ============================================================

def get_message_content(message):
    try:
        return message.find_element(By.CSS_SELECTOR, ":scope > div.KB4Aq")
    except Exception:
        return None


# ============================================================
# FIND VISIBLE SNAP CANDIDATES
# ============================================================

def find_visible_snaps(driver, pane):
    """
    Returns only visible media/Snap candidates, each tagged with a
    stable temporary ID so the same Snap isn't reprocessed after
    scroll overlap.
    """

    script = """
    const pane = arguments[0];
    const viewportHeight = window.innerHeight;

    const ignoredPhrases = [
        "you deleted a snap", "you deleted a chat", "this message was deleted",
        "your snapstreak ended", "started a snapstreak",
        "not supported on web", "check from your phone"
    ];

    if (!window.__snapCleanerCounter) window.__snapCleanerCounter = 1;

    const results = [];
    const seen = new Set();

    for (const item of pane.querySelectorAll("li")) {
        try {
            const content = item.querySelector(":scope > div.KB4Aq");
            if (!content) continue;

            const rect = content.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) continue;
            if (rect.bottom < 0 || rect.top > viewportHeight) continue;

            const text = (item.innerText || "").trim().replace(/\\s+/g, " ").toLowerCase();
            if (ignoredPhrases.some(phrase => text.includes(phrase))) continue;

            const media = content.querySelector("img, video");
            if (!media) continue;

            if (!item.dataset.snapCleanerId) {
                item.dataset.snapCleanerId = "snap-cleaner-" + window.__snapCleanerCounter++;
            }

            const id = item.dataset.snapCleanerId;
            if (seen.has(id)) continue;
            seen.add(id);

            results.push({ element: item, id: id, top: rect.top, text: text });
        } catch (error) {
            continue;
        }
    }

    results.sort((a, b) => a.top - b.top);
    return results;
    """

    try:
        return driver.execute_script(script, pane)
    except Exception:
        return []


# ============================================================
# HOVER SNAP
# ============================================================

def hover_message(driver, message):
    """Hover only. Never click the Snap/media itself."""
    try:
        content = get_message_content(message)
        if content is None:
            return False

        ActionChains(driver).move_to_element(content).perform()
        return True
    except (StaleElementReferenceException, WebDriverException):
        return False


# ============================================================
# FIND AVAILABLE ACTION
# ============================================================

def find_available_action(driver, message):
    """
    Checks for Delete and Unsave in one pass. Delete takes priority.
    SVG path fingerprints are checked first; aria-label/title/
    data-testid/text are a fallback for future UI changes.
    """

    script = """
    const message = arguments[0];
    const trashMarker = arguments[1];
    const unsaveMarkers = arguments[2];

    const toolbar = message.querySelector(".Bhzh6");
    if (!toolbar) return { found: false, reason: "no_toolbar" };

    let buttons = Array.from(toolbar.querySelectorAll("button.NcaQH"));
    if (buttons.length === 0) buttons = Array.from(toolbar.querySelectorAll("button"));

    function getButtonInfo(button) {
        const attributes = [
            button.getAttribute("aria-label") || "",
            button.getAttribute("title") || "",
            button.getAttribute("data-testid") || "",
            button.getAttribute("data-action") || "",
            button.innerText || ""
        ].join(" ").toLowerCase();

        let isTrash = false, isUnsave = false;

        for (const path of button.querySelectorAll("svg path")) {
            const d = path.getAttribute("d") || "";
            if (d.startsWith(trashMarker) || d.includes(trashMarker)) isTrash = true;
            for (const marker of unsaveMarkers) {
                if (d.startsWith(marker) || d.includes(marker)) isUnsave = true;
            }
        }

        if (attributes.includes("delete") || attributes.includes("trash")) isTrash = true;
        if (attributes.includes("unsave")) isUnsave = true;

        return { trash: isTrash, unsave: isUnsave };
    }

    for (let i = 0; i < buttons.length; i++) {
        if (getButtonInfo(buttons[i]).trash) return { found: true, action: "delete", index: i };
    }

    for (let i = 0; i < buttons.length; i++) {
        if (getButtonInfo(buttons[i]).unsave) return { found: true, action: "unsave", index: i };
    }

    return { found: false, reason: "no_action" };
    """

    try:
        return driver.execute_script(script, message, TRASH_PATH_MARKER, list(UNSAVE_PATH_MARKERS))
    except Exception:
        return {"found": False}


# ============================================================
# CLICK ACTION
# ============================================================

def click_available_action(driver, message, action_index):
    script = """
    const message = arguments[0];
    const index = arguments[1];

    const toolbar = message.querySelector(".Bhzh6");
    if (!toolbar) return { clicked: false };

    let buttons = Array.from(toolbar.querySelectorAll("button.NcaQH"));
    if (buttons.length === 0) buttons = Array.from(toolbar.querySelectorAll("button"));

    if (index < 0 || index >= buttons.length) return { clicked: false };

    buttons[index].click();
    return { clicked: true };
    """

    try:
        return driver.execute_script(script, message, action_index)
    except Exception:
        return {"clicked": False}


# ============================================================
# PROCESS ONE SNAP
# ============================================================

def process_snap(driver, message):
    """Returns: mutated | no_action | stale"""

    try:
        if not hover_message(driver, message):
            return "stale"

        deadline = time.perf_counter() + ACTION_TIMEOUT
        action = {"found": False}

        while time.perf_counter() < deadline:
            action = find_available_action(driver, message)
            if action.get("found"):
                break
            time.sleep(ACTION_POLL_INTERVAL)

        if not action.get("found"):
            return "no_action"

        result = click_available_action(driver, message, action["index"])
        if not result.get("clicked"):
            return "no_action"

        print(f"{action['action'].upper()} ✓ button={action['index']}")
        time.sleep(POST_ACTION_WAIT)

        return "mutated"

    except (StaleElementReferenceException, WebDriverException):
        return "stale"
    except Exception as error:
        print("Item error:", error)
        return "no_action"


# ============================================================
# SCROLLING
# ============================================================

def find_scroll_container(driver, pane):
    script = """
    let element = arguments[0];
    while (element) {
        const style = window.getComputedStyle(element);
        if (
            (style.overflowY === "auto" || style.overflowY === "scroll") &&
            element.scrollHeight > element.clientHeight + 10
        ) {
            return element;
        }
        element = element.parentElement;
    }
    return null;
    """

    try:
        return driver.execute_script(script, pane)
    except Exception:
        return None


def scroll_up(driver, container):
    script = """
    const element = arguments[0];
    const ratio = arguments[1];
    const before = element.scrollTop;
    const distance = Math.max(350, element.clientHeight * ratio);
    element.scrollTop = Math.max(0, before - distance);
    return { before: before, after: element.scrollTop, clientHeight: element.clientHeight };
    """

    return driver.execute_script(script, container, SCROLL_RATIO)


# ============================================================
# MAIN
# ============================================================

def main():
    driver = connect_to_chrome()
    locked_url = get_locked_url(driver)

    stats = {"actions": 0, "scrolls": 0, "scans": 0, "no_action": 0, "already_checked": 0}
    attempts = {}       # how many times a Snap was inspected without an action
    completed = set()   # Snaps fully checked in previous overlapping viewports

    print()
    print("=" * 65)
    print("SNAPCHAT FAST CLEANER - OPTIMIZED")
    print("=" * 65)
    print("Mode: Bottom → Top")
    print("Target: Media/Snaps only")
    print("Messages: Ignored")
    print("Actions: Delete + Unsave")
    print("Scroll: Automatic")
    print("Speed: Optimized")
    print("Press CTRL+C to stop.")
    print("=" * 65)

    try:
        while True:
            if not conversation_is_locked(driver, locked_url):
                print("Conversation changed. Stopping.")
                break

            pane = get_conversation_pane(driver, locked_url)
            if pane is None:
                time.sleep(0.05)
                continue

            stats["scans"] += 1
            snaps = find_visible_snaps(driver, pane)
            dom_changed = False

            # Process bottom to top so deletions don't shift
            # not-yet-processed elements out from under us.
            for candidate in reversed(snaps):
                snap_id = candidate["id"]

                if snap_id in completed:
                    stats["already_checked"] += 1
                    continue

                result = process_snap(driver, candidate["element"])

                if result == "mutated":
                    stats["actions"] += 1
                    completed.add(snap_id)
                    dom_changed = True  # DOM may have changed, rescan
                    break

                if result == "stale":
                    dom_changed = True
                    break

                # no_action
                attempts[snap_id] = attempts.get(snap_id, 0) + 1
                stats["no_action"] += 1

                if attempts[snap_id] >= MAX_NO_ACTION_ATTEMPTS:
                    completed.add(snap_id)

            if dom_changed:
                continue

            # Current view fully processed — try to scroll further up.
            pane = get_conversation_pane(driver, locked_url)
            if pane is None:
                continue

            container = find_scroll_container(driver, pane)
            if container is None:
                print("Scroll container not found.")
                break

            scroll_top = driver.execute_script("return arguments[0].scrollTop;", container)

            if scroll_top <= 1:
                print()
                print("TOP OF CHAT REACHED.")
                break

            scroll_up(driver, container)
            stats["scrolls"] += 1
            print(f"↑ Scroll {stats['scrolls']} | Actions: {stats['actions']}")
            time.sleep(POST_SCROLL_WAIT)

    except KeyboardInterrupt:
        print()
        print("Stopped by user.")

    finally:
        print()
        print("=" * 65)
        print("FINAL RESULT")
        print("=" * 65)
        print("Actions:", stats["actions"])
        print("Scrolls:", stats["scrolls"])
        print("Scans:", stats["scans"])
        print("No action:", stats["no_action"])
        print("Already checked:", stats["already_checked"])


if __name__ == "__main__":
    main()