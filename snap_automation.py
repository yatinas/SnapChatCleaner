import time
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    StaleElementReferenceException,
    WebDriverException,
)


# ============================================================
# CONFIGURATION
# ============================================================

DEBUGGER_ADDRESS = "127.0.0.1:9222"

# Fast hover/action detection.
ACTION_POLL_INTERVAL = 0.02
ACTION_TIMEOUT = 0.16

# Small wait after clicking an action.
POST_ACTION_WAIT = 0.05

# Small wait after scrolling.
POST_SCROLL_WAIT = 0.06

# Scroll overlap prevents missing Snaps at viewport boundaries.
SCROLL_RATIO = 0.88

# Existing working Trash SVG marker.
TRASH_PATH_MARKER = "M9.75 4.624"

# Existing Unsave SVG marker.
UNSAVE_PATH_MARKERS = (
    "M18.742 4.576",
)

MEDIA_SELECTOR = "img, video"

# Number of times a Snap can be inspected without an action
# before it is considered checked.
MAX_NO_ACTION_ATTEMPTS = 2


# ============================================================
# CONNECT TO EXISTING CHROME
# ============================================================

def connect_to_chrome():
    options = Options()
    options.add_experimental_option(
        "debuggerAddress",
        DEBUGGER_ADDRESS
    )

    print("Connecting to Chrome...")

    driver = webdriver.Chrome(
        options=options
    )

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
        raise RuntimeError(
            "Snapchat Web is not open."
        )

    if not parsed.path.startswith("/web/"):
        raise RuntimeError(
            "Open the Snapchat conversation first."
        )

    return url


def conversation_is_locked(driver, locked_url):
    return driver.current_url == locked_url


def get_conversation_pane(driver, locked_url):
    parsed = urlparse(locked_url)

    parts = [
        part
        for part in parsed.path.split("/")
        if part
    ]

    if len(parts) < 2:
        return None

    conversation_id = parts[1]

    try:
        return driver.find_element(
            By.ID,
            f"cv-{conversation_id}"
        )

    except Exception:
        return None


# ============================================================
# TEXT HELPERS
# ============================================================

def should_ignore(text):
    if not text:
        return False

    normalized = text.lower().strip()

    ignored_phrases = (
        "you deleted a snap",
        "you deleted a chat",
        "this message was deleted",
        "your snapstreak ended",
        "started a snapstreak",
        "not supported on web",
        "check from your phone",
    )

    for phrase in ignored_phrases:
        if phrase in normalized:
            return True

    return False


# ============================================================
# MESSAGE CONTENT
# ============================================================

def get_message_content(message):
    try:
        return message.find_element(
            By.CSS_SELECTOR,
            ":scope > div.KB4Aq"
        )

    except Exception:
        return None


# ============================================================
# FIND VISIBLE SNAP CANDIDATES
# ============================================================

def find_visible_snaps(driver, pane):
    """
    Returns only visible media/Snap candidates.

    Each DOM item gets a temporary unique ID so the same
    Snap is not unnecessarily processed again because of
    scroll overlap.
    """

    script = """
    const pane = arguments[0];
    const viewportHeight = window.innerHeight;

    const ignoredPhrases = [
        "you deleted a snap",
        "you deleted a chat",
        "this message was deleted",
        "your snapstreak ended",
        "started a snapstreak",
        "not supported on web",
        "check from your phone"
    ];

    if (!window.__snapCleanerCounter) {
        window.__snapCleanerCounter = 1;
    }

    const results = [];
    const seen = new Set();

    const items = pane.querySelectorAll("li");

    for (const item of items) {

        try {
            const content =
                item.querySelector(":scope > div.KB4Aq");

            if (!content) {
                continue;
            }

            const rect =
                content.getBoundingClientRect();

            if (
                rect.width <= 0 ||
                rect.height <= 0
            ) {
                continue;
            }

            if (
                rect.bottom < 0 ||
                rect.top > viewportHeight
            ) {
                continue;
            }

            const text =
                (item.innerText || "")
                .trim()
                .replace(/\\s+/g, " ")
                .toLowerCase();

            let ignored = false;

            for (const phrase of ignoredPhrases) {
                if (text.includes(phrase)) {
                    ignored = true;
                    break;
                }
            }

            if (ignored) {
                continue;
            }

            const media =
                content.querySelector(
                    "img, video"
                );

            if (!media) {
                continue;
            }

            if (
                !item.dataset.snapCleanerId
            ) {
                item.dataset.snapCleanerId =
                    "snap-cleaner-" +
                    window.__snapCleanerCounter++;
            }

            const id =
                item.dataset.snapCleanerId;

            if (seen.has(id)) {
                continue;
            }

            seen.add(id);

            results.push({
                element: item,
                id: id,
                top: rect.top,
                text: text
            });

        } catch (error) {
            continue;
        }
    }

    results.sort(
        (a, b) => a.top - b.top
    );

    return results;
    """

    try:
        return driver.execute_script(
            script,
            pane
        )

    except Exception:
        return []


# ============================================================
# HOVER SNAP
# ============================================================

def hover_message(driver, message):
    """
    Hover only.

    Never click the Snap/media itself.
    """

    try:
        content = get_message_content(
            message
        )

        if content is None:
            return False

        ActionChains(driver).move_to_element(
            content
        ).perform()

        return True

    except (
        StaleElementReferenceException,
        WebDriverException,
    ):
        return False


# ============================================================
# FIND AVAILABLE ACTION
# ============================================================

def find_available_action(driver, message):
    """
    Checks Delete and Unsave in one operation.

    Priority:
    1. Delete
    2. Unsave

    SVG markers are used first.

    aria-label/title/data-testid/text are used as
    fallback detection for future Snapchat UI changes.
    """

    script = """
    const message = arguments[0];
    const trashMarker = arguments[1];
    const unsaveMarkers = arguments[2];

    const toolbar =
        message.querySelector(".Bhzh6");

    if (!toolbar) {
        return {
            found: false,
            reason: "no_toolbar"
        };
    }

    let buttons =
        Array.from(
            toolbar.querySelectorAll(
                "button.NcaQH"
            )
        );

    if (buttons.length === 0) {
        buttons =
            Array.from(
                toolbar.querySelectorAll(
                    "button"
                )
            );
    }

    function getButtonInfo(button) {

        const attributes = [
            button.getAttribute("aria-label") || "",
            button.getAttribute("title") || "",
            button.getAttribute("data-testid") || "",
            button.getAttribute("data-action") || "",
            button.innerText || ""
        ]
        .join(" ")
        .toLowerCase();

        const paths =
            button.querySelectorAll(
                "svg path"
            );

        let isTrash = false;
        let isUnsave = false;

        for (const path of paths) {

            const d =
                path.getAttribute("d") || "";

            if (
                d.startsWith(trashMarker) ||
                d.includes(trashMarker)
            ) {
                isTrash = true;
            }

            for (
                const marker of unsaveMarkers
            ) {
                if (
                    d.startsWith(marker) ||
                    d.includes(marker)
                ) {
                    isUnsave = true;
                }
            }
        }

        if (
            attributes.includes("delete") ||
            attributes.includes("trash")
        ) {
            isTrash = true;
        }

        if (
            attributes.includes("unsave")
        ) {
            isUnsave = true;
        }

        return {
            trash: isTrash,
            unsave: isUnsave
        };
    }

    // --------------------------------------------------------
    // DELETE FIRST
    // --------------------------------------------------------

    for (
        let i = 0;
        i < buttons.length;
        i++
    ) {
        const info =
            getButtonInfo(buttons[i]);

        if (info.trash) {
            return {
                found: true,
                action: "delete",
                index: i
            };
        }
    }

    // --------------------------------------------------------
    // THEN UNSAVE
    // --------------------------------------------------------

    for (
        let i = 0;
        i < buttons.length;
        i++
    ) {
        const info =
            getButtonInfo(buttons[i]);

        if (info.unsave) {
            return {
                found: true,
                action: "unsave",
                index: i
            };
        }
    }

    return {
        found: false,
        reason: "no_action"
    };
    """

    try:
        return driver.execute_script(
            script,
            message,
            TRASH_PATH_MARKER,
            list(UNSAVE_PATH_MARKERS)
        )

    except Exception:
        return {
            "found": False
        }


# ============================================================
# CLICK ACTION
# ============================================================

def click_available_action(
    driver,
    message,
    action_index
):
    script = """
    const message = arguments[0];
    const index = arguments[1];

    const toolbar =
        message.querySelector(".Bhzh6");

    if (!toolbar) {
        return {
            clicked: false
        };
    }

    let buttons =
        Array.from(
            toolbar.querySelectorAll(
                "button.NcaQH"
            )
        );

    if (buttons.length === 0) {
        buttons =
            Array.from(
                toolbar.querySelectorAll(
                    "button"
                )
            );
    }

    if (
        index < 0 ||
        index >= buttons.length
    ) {
        return {
            clicked: false
        };
    }

    const button =
        buttons[index];

    button.click();

    return {
        clicked: true
    };
    """

    try:
        return driver.execute_script(
            script,
            message,
            action_index
        )

    except Exception:
        return {
            "clicked": False
        }


# ============================================================
# PROCESS ONE SNAP
# ============================================================

def process_snap(driver, message):
    """
    Returns:

    mutated
    no_action
    stale
    """

    try:
        if not hover_message(
            driver,
            message
        ):
            return "stale"

        deadline = (
            time.perf_counter() +
            ACTION_TIMEOUT
        )

        action = {
            "found": False
        }

        while (
            time.perf_counter() <
            deadline
        ):
            action = find_available_action(
                driver,
                message
            )

            if action.get("found"):
                break

            time.sleep(
                ACTION_POLL_INTERVAL
            )

        if not action.get("found"):
            return "no_action"

        result = click_available_action(
            driver,
            message,
            action["index"]
        )

        if not result.get("clicked"):
            return "no_action"

        if action["action"] == "delete":
            print(
                "DELETE ✓ "
                f"button={action['index']}"
            )

        elif action["action"] == "unsave":
            print(
                "UNSAVE ✓ "
                f"button={action['index']}"
            )

        time.sleep(
            POST_ACTION_WAIT
        )

        return "mutated"

    except (
        StaleElementReferenceException,
        WebDriverException,
    ):
        return "stale"

    except Exception as error:
        print(
            "Item error:",
            error
        )

        return "no_action"


# ============================================================
# FIND SCROLL CONTAINER
# ============================================================

def find_scroll_container(
    driver,
    pane
):
    script = """
    let element = arguments[0];

    while (element) {

        const style =
            window.getComputedStyle(
                element
            );

        const overflow =
            style.overflowY;

        if (
            (
                overflow === "auto" ||
                overflow === "scroll"
            ) &&
            element.scrollHeight >
            element.clientHeight + 10
        ) {
            return element;
        }

        element =
            element.parentElement;
    }

    return null;
    """

    try:
        return driver.execute_script(
            script,
            pane
        )

    except Exception:
        return None


# ============================================================
# SCROLL UP
# ============================================================

def scroll_up(
    driver,
    container
):
    script = """
    const element = arguments[0];
    const ratio = arguments[1];

    const before =
        element.scrollTop;

    const distance =
        Math.max(
            350,
            element.clientHeight * ratio
        );

    element.scrollTop =
        Math.max(
            0,
            before - distance
        );

    return {
        before: before,
        after: element.scrollTop,
        clientHeight:
            element.clientHeight
    };
    """

    return driver.execute_script(
        script,
        container,
        SCROLL_RATIO
    )


# ============================================================
# MAIN
# ============================================================

def main():
    driver = connect_to_chrome()

    locked_url = get_locked_url(
        driver
    )

    stats = {
        "actions": 0,
        "scrolls": 0,
        "scans": 0,
        "no_action": 0,
        "already_checked": 0,
    }

    # Tracks how many times a Snap was inspected.
    attempts = {}

    # Items fully checked in previous overlapping viewports.
    completed = set()

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

            # ------------------------------------------------
            # CONVERSATION SAFETY
            # ------------------------------------------------

            if not conversation_is_locked(
                driver,
                locked_url
            ):
                print(
                    "Conversation changed. "
                    "Stopping."
                )

                break

            pane = get_conversation_pane(
                driver,
                locked_url
            )

            if pane is None:
                time.sleep(0.05)
                continue

            stats["scans"] += 1

            snaps = find_visible_snaps(
                driver,
                pane
            )

            # ------------------------------------------------
            # PROCESS BOTTOM → TOP
            # ------------------------------------------------

            dom_changed = False

            for candidate in reversed(snaps):

                snap_id =candidate["id"]

                # ------------------------------------------------
                # ALREADY COMPLETELY CHECKED
                # ------------------------------------------------

                if snap_id in completed:
                    stats[
                        "already_checked"
                    ] += 1

                    continue

                message =candidate["element"]

                result = process_snap(
                    driver,
                    message
                )

                # ------------------------------------------------
                # ACTION PERFORMED
                # ------------------------------------------------

                if result == "mutated":
                    stats["actions"] += 1

                    completed.add(
                        snap_id
                    )

                    # DOM may have changed.
                    # Immediately rescan.
                    dom_changed = True

                    break

                # ------------------------------------------------
                # STALE DOM
                # ------------------------------------------------

                if result == "stale":
                    dom_changed = True
                    break

                # ------------------------------------------------
                # NO ACTION
                # ------------------------------------------------

                attempts[snap_id] = (
                    attempts.get(
                        snap_id,
                        0
                    ) + 1
                )

                stats["no_action"] += 1

                if (
                    attempts[snap_id] >=
                    MAX_NO_ACTION_ATTEMPTS
                ):
                    completed.add(
                        snap_id
                    )

            # ------------------------------------------------
            # DOM CHANGED
            # ------------------------------------------------

            if dom_changed:
                continue

            # ------------------------------------------------
            # CURRENT VIEW COMPLETE
            # ------------------------------------------------

            pane = get_conversation_pane(
                driver,
                locked_url
            )

            if pane is None:
                continue

            container = find_scroll_container(
                driver,
                pane
            )

            if container is None:
                print(
                    "Scroll container not found."
                )

                break

            scroll_top = (
                driver.execute_script(
                    "return arguments[0].scrollTop;",
                    container
                )
            )

            # ------------------------------------------------
            # TOP REACHED
            # ------------------------------------------------

            if scroll_top <= 1:
                print()
                print(
                    "TOP OF CHAT REACHED."
                )

                break

            scroll_result = scroll_up(
                driver,
                container
            )

            stats["scrolls"] += 1

            print(
                f"↑ Scroll "
                f"{stats['scrolls']} | "
                f"Actions: "
                f"{stats['actions']}"
            )

            time.sleep(
                POST_SCROLL_WAIT
            )

    except KeyboardInterrupt:
        print()
        print(
            "Stopped by user."
        )

    finally:
        print()
        print("=" * 65)
        print("FINAL RESULT")
        print("=" * 65)
        print(
            "Actions:",
            stats["actions"]
        )
        print(
            "Scrolls:",
            stats["scrolls"]
        )
        print(
            "Scans:",
            stats["scans"]
        )
        print(
            "No action:",
            stats["no_action"]
        )
        print(
            "Already checked:",
            stats["already_checked"]
        )


if __name__ == "__main__":
    main()