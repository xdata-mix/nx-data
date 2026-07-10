#!/usr/bin/env python3
"""
Stream4Free scraper — nodriver (CDP direct) + Xvfb virtual display.

CF Turnstile bypass via genuine Chrome (non-headful) on a virtual display.
nodriver = official successor of undetected-chromedriver, communicates via CDP,
no Selenium/WebDriver detection vectors.

Key insight: CF detects headless mode → we run headful Chrome on Xvfb.
"""

import asyncio
import logging
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# Force stdout flush (GitHub Actions buffers)
_orig_print = print
def print(*a, **kw):
    kw.setdefault("flush", True)
    _orig_print(*a, **kw)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PAGE_URL = "https://www.stream4free.tv/tv-live-france"
M3U_FILE = Path("data-stream4free.m3u")
MAX_RETRIES = 3
CF_WAIT_INITIAL = 8            # seconds to wait after first page load
CF_WAIT_AFTER_CLICK = 10       # seconds to wait after clicking Turnstile
CF_WAIT_EXTRA = 5              # extra wait for JS render
PAGE_LOAD_WAIT = 5             # seconds to wait for JS rendering
CF_INDICATORS = [
    "just a moment",
    "checking your browser",
    "turnstile",
    "cf-challenge",
    "cf_clearance",
    "security check",
    "please wait",
    "vérification",
    "enable javascript",
]

# ---------------------------------------------------------------------------
# HTML parser for channel extraction
# ---------------------------------------------------------------------------
class ChannelParser(HTMLParser):
    """Extract channel name + m3u8/mpd URLs from stream4free HTML."""

    def __init__(self):
        super().__init__()
        self.channels = []  # list of (name, url)
        self._in_a = False
        self._current_href = None
        self._current_text = ""

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            d = dict(attrs)
            href = d.get("href", "")
            if "/tv-live-france/" in href or "/channel/" in href:
                self._in_a = True
                self._current_href = href
                self._current_text = ""

    def handle_data(self, data):
        if self._in_a:
            self._current_text += data

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            name = self._current_text.strip()
            if name and self._current_href:
                self.channels.append((name, self._current_href))
            self._in_a = False
            self._current_href = None
            self._current_text = ""


def extract_channels_from_html(html: str) -> list[tuple[str, str]]:
    """Parse the listing page and return (channel_name, channel_page_url)."""
    parser = ChannelParser()
    parser.feed(html)
    return parser.channels


def extract_stream_urls(html: str) -> list[str]:
    """Pull m3u8/mpd URLs from a channel detail page."""
    patterns = [
        r'(?:source|src|file|url)\s*[:=]\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'(?:source|src|file|url)\s*[:=]\s*["\']([^"\']+\.mpd[^"\']*)["\']',
        r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.mpd[^\s"\'<>]*)',
    ]
    urls = []
    seen = set()
    for pat in patterns:
        for m in re.finditer(pat, html, re.IGNORECASE):
            u = m.group(1)
            if u not in seen:
                seen.add(u)
                urls.append(u)
    return urls


# ---------------------------------------------------------------------------
# Virtual display helper
# ---------------------------------------------------------------------------
def setup_virtual_display():
    """Start Xvfb virtual display on Linux (for CI). Returns Display or None."""
    if sys.platform != "linux":
        log.info("Not Linux — skipping virtual display setup")
        return None
    # Check if a real display is available
    if os.environ.get("DISPLAY"):
        log.info("DISPLAY already set (%s) — skipping Xvfb", os.environ["DISPLAY"])
        return None
    try:
        from pyvirtualdisplay import Display
        display = Display(visible=False, size=(1920, 1080))
        display.start()
        log.info("Virtual display started (Xvfb) — DISPLAY=%s", os.environ.get("DISPLAY"))
        return display
    except Exception as e:
        log.warning("Failed to start virtual display: %s — trying headless fallback", e)
        return None


def is_cf_challenge(html: str) -> bool:
    """Check if the page is a Cloudflare challenge page."""
    if not html:
        return True
    lower = html.lower()
    # Check for clear CF challenge indicators
    cf_strong = ["just a moment", "cf-challenge", "checking your browser", "turnstile"]
    return any(ind in lower for ind in cf_strong)


def is_real_content(html: str) -> bool:
    """Check if we got the real stream4free page (not CF challenge)."""
    if not html or len(html) < 3000:
        return False
    lower = html.lower()
    # Real page should have channel-related content
    content_markers = ["stream4free", "tv-live", "channel", "m3u8", ".mpd", "iframe"]
    return any(m in lower for m in content_markers) and not is_cf_challenge(html)


# ---------------------------------------------------------------------------
# Core: nodriver CF bypass + page fetch
# ---------------------------------------------------------------------------
async def launch_browser_with_retry(headless: bool = False, max_attempts: int = 3):
    """Launch nodriver browser with retry for flaky CDP connections."""
    import nodriver as uc
    import shutil

    # Find Chrome binary — prefer google-chrome-stable over snap chromium
    chrome_path = (
        shutil.which("google-chrome-stable")
        or shutil.which("google-chrome")
        or shutil.which("chromium-browser")
        or shutil.which("chromium")
    )

    browser_args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-features=Translate",
        "--disable-blink-features=AutomationControlled",
        "--disable-extensions",
        "--disable-popup-blocking",
        "--disable-infobars",
        "--lang=fr-FR",
        "--window-size=1920,1080",
    ]

    for attempt in range(1, max_attempts + 1):
        try:
            log.info("Browser launch attempt %d/%d (headless=%s, binary=%s)",
                     attempt, max_attempts, headless, chrome_path)
            browser = await uc.start(
                headless=headless,
                sandbox=False,
                browser_executable_path=chrome_path,
                browser_args=browser_args,
                lang="fr-FR",
            )
            log.info("Browser connected successfully (attempt %d)", attempt)
            return browser
        except Exception as e:
            log.warning("Browser launch attempt %d failed: %s", attempt, e)
            if attempt < max_attempts:
                await asyncio.sleep(2)
            else:
                raise


async def try_click_turnstile(tab) -> bool:
    """
    Try to find and click the Cloudflare Turnstile checkbox.
    Returns True if we found and clicked something.
    """
    try:
        # Method 1: Find Turnstile iframe and click its checkbox
        # The Turnstile widget is typically in an iframe with src containing challenges.cloudflare.com
        log.info("Looking for Turnstile iframe...")

        # Try to find the Turnstile iframe via JS
        result = await tab.evaluate("""
            (() => {
                // Look for Turnstile iframe
                const iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]');
                if (iframes.length > 0) {
                    const iframe = iframes[0];
                    const rect = iframe.getBoundingClientRect();
                    return {found: true, x: rect.x + rect.width/2, y: rect.y + rect.height/2,
                            width: rect.width, height: rect.height, src: iframe.src.substring(0, 100)};
                }
                // Also look for cf-turnstile div
                const cfDiv = document.querySelector('.cf-turnstile, #cf-turnstile, [class*="turnstile"]');
                if (cfDiv) {
                    const rect = cfDiv.getBoundingClientRect();
                    return {found: true, x: rect.x + rect.width/2, y: rect.y + rect.height/2,
                            width: rect.width, height: rect.height, src: 'cf-turnstile-div'};
                }
                return {found: false};
            })()
        """)

        if result and result.get("found"):
            log.info("Found Turnstile element: %s (pos: %d,%d size: %dx%d)",
                     result.get("src", "?"), result.get("x", 0), result.get("y", 0),
                     result.get("width", 0), result.get("height", 0))

            # Click in the center of the Turnstile widget
            x = int(result.get("x", 300))
            y = int(result.get("y", 300))

            # Use mouse click at the coordinates
            await tab.send({"method": "Input.dispatchMouseEvent", "params": {
                "type": "mousePressed", "x": x, "y": y, "button": "left",
                "clickCount": 1, "buttons": 1
            }})
            await asyncio.sleep(0.1)
            await tab.send({"method": "Input.dispatchMouseEvent", "params": {
                "type": "mouseReleased", "x": x, "y": y, "button": "left",
                "clickCount": 1, "buttons": 0
            }})
            log.info("Clicked Turnstile at (%d, %d)", x, y)
            return True
        else:
            log.info("No Turnstile iframe/div found on page")

        # Method 2: Try clicking on common CF challenge button positions
        # Some CF challenges have a visible "Verify you are human" button
        result2 = await tab.evaluate("""
            (() => {
                const btns = document.querySelectorAll('input[type="button"], button, .big-button, #challenge-stage');
                for (const btn of btns) {
                    const text = (btn.textContent || btn.value || '').toLowerCase();
                    if (text.includes('verify') || text.includes('human') || text.includes('vérif')) {
                        const rect = btn.getBoundingClientRect();
                        return {found: true, x: rect.x + rect.width/2, y: rect.y + rect.height/2, text: text.substring(0,50)};
                    }
                }
                return {found: false};
            })()
        """)

        if result2 and result2.get("found"):
            x = int(result2.get("x", 300))
            y = int(result2.get("y", 300))
            log.info("Found verify button: '%s' at (%d,%d)", result2.get("text"), x, y)
            await tab.send({"method": "Input.dispatchMouseEvent", "params": {
                "type": "mousePressed", "x": x, "y": y, "button": "left",
                "clickCount": 1, "buttons": 1
            }})
            await asyncio.sleep(0.1)
            await tab.send({"method": "Input.dispatchMouseEvent", "params": {
                "type": "mouseReleased", "x": x, "y": y, "button": "left",
                "clickCount": 1, "buttons": 0
            }})
            log.info("Clicked verify button at (%d, %d)", x, y)
            return True

    except Exception as e:
        log.warning("Error trying to click Turnstile: %s", e)

    return False


async def fetch_page_with_nodriver(url: str, headless: bool = False) -> str | None:
    """
    Open *url* in genuine Chrome via nodriver, handle CF Turnstile, return HTML.
    headless=False is CRITICAL — CF detects headless mode.
    """
    browser = await launch_browser_with_retry(headless=headless)

    try:
        log.info("Navigating to %s", url)
        tab = await browser.get(url)
        await asyncio.sleep(PAGE_LOAD_WAIT)

        # Get page content and check for CF
        page_content = await tab.get_content()
        content_len = len(page_content) if page_content else 0
        log.info("Initial page content: %d chars", content_len)

        # Log first 500 chars for diagnostic
        if page_content:
            snippet = page_content[:500].replace('\n', ' ').replace('\r', '')
            log.info("Page snippet: %s", snippet)

        # Check for CF challenge
        if is_cf_challenge(page_content):
            log.info("CF challenge detected — attempting Turnstile bypass")

            # Step 1: Wait a bit for Turnstile to render
            await asyncio.sleep(3)

            # Step 2: Try to click the Turnstile checkbox
            clicked = await try_click_turnstile(tab)

            if clicked:
                log.info("Waiting %ds after Turnstile click...", CF_WAIT_AFTER_CLICK)
                await asyncio.sleep(CF_WAIT_AFTER_CLICK)
            else:
                log.info("No clickable Turnstile found — waiting %ds for auto-solve...", CF_WAIT_INITIAL)
                await asyncio.sleep(CF_WAIT_INITIAL)

            # Step 3: Check if we passed
            page_content = await tab.get_content()
            content_len = len(page_content) if page_content else 0
            log.info("After Turnstile attempt: %d chars", content_len)

            if is_cf_challenge(page_content):
                # Step 4: Try re-navigating (cookies should be set)
                log.info("Still on CF — re-navigating with cookies")
                tab = await browser.get(url)
                await asyncio.sleep(PAGE_LOAD_WAIT)
                page_content = await tab.get_content()
                content_len = len(page_content) if page_content else 0
                log.info("After re-navigation: %d chars", content_len)

                if is_cf_challenge(page_content):
                    # Step 5: One more Turnstile click attempt
                    await asyncio.sleep(3)
                    clicked2 = await try_click_turnstile(tab)
                    if clicked2:
                        await asyncio.sleep(CF_WAIT_AFTER_CLICK)
                        page_content = await tab.get_content()
                        content_len = len(page_content) if page_content else 0
                        log.info("After 2nd Turnstile click: %d chars", content_len)
        else:
            log.info("No CF challenge detected — page loaded directly")

        # Final content check
        if page_content and is_real_content(page_content):
            log.info("SUCCESS: Got real page content (%d chars)", len(page_content))
        elif page_content:
            # Log diagnostic info about what we got
            title_match = re.search(r'<title>(.*?)</title>', page_content, re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else "NO TITLE"
            log.warning("Page title: %s", title)
            log.warning("Page is_cf=%s, is_real=%s, len=%d",
                       is_cf_challenge(page_content), is_real_content(page_content), len(page_content))
            # Log a bigger snippet
            snippet = page_content[:1000].replace('\n', ' ').replace('\r', '')
            log.info("Full snippet: %s", snippet)

        return page_content

    finally:
        try:
            browser.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main scraper logic
# ---------------------------------------------------------------------------
async def scrape_stream4free() -> list[tuple[str, str]]:
    """
    Scrape stream4free.tv and return list of (channel_name, stream_url).
    Uses nodriver with Xvfb virtual display.
    """
    display = setup_virtual_display()
    use_headless = display is None and sys.platform == "linux"

    if use_headless:
        log.warning("No virtual display available — falling back to headless (less reliable)")

    results = []

    for attempt in range(1, MAX_RETRIES + 1):
        log.info("=== Attempt %d/%d ===", attempt, MAX_RETRIES)

        try:
            html = await fetch_page_with_nodriver(PAGE_URL, headless=use_headless)

            if not html or len(html) < 2000:
                log.warning("Attempt %d: page too short (%d chars)", attempt, len(html) if html else 0)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(5)
                continue

            # Check we actually got past CF
            if is_cf_challenge(html):
                log.warning("Attempt %d: still on CF challenge page", attempt)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(5)
                continue

            # Check for real content
            if not is_real_content(html):
                log.warning("Attempt %d: got page but no stream4free content markers", attempt)
                # Still try to extract what we can

            # Extract channel links from listing
            channels = extract_channels_from_html(html)
            log.info("Found %d channel links on listing page", len(channels))

            if not channels:
                # Fallback: try regex for any internal links
                links = re.findall(
                    r'href=["\']([^"\']*(?:/tv-live-france/|/channel/)[^"\']*)["\']',
                    html, re.IGNORECASE
                )
                log.info("Regex fallback found %d links", len(links))

            # Also try to extract stream URLs directly from the listing page
            direct_streams = extract_stream_urls(html)
            if direct_streams:
                log.info("Found %d direct stream URLs on listing page", len(direct_streams))

            # Log what we found
            log.info("Listing page HTML snippet (first 500 chars):\n%s", html[:500])

            # Parse the full page for m3u8 links in any form
            all_m3u8 = re.findall(
                r'(https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*)', html, re.IGNORECASE
            )
            log.info("Total m3u8 URLs found in page: %d", len(all_m3u8))
            for u in all_m3u8[:10]:
                log.info("  m3u8: %s", u)

            if len(html) > 5000:
                log.info("Got substantial page content (%d chars) — CF bypass SUCCESS", len(html))
                break
            else:
                log.warning("Page content only %d chars — may be incomplete", len(html))

        except Exception as e:
            log.error("Attempt %d failed: %s", attempt, e, exc_info=True)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(5)

    # Cleanup virtual display
    if display:
        try:
            display.stop()
        except Exception:
            pass

    return results


def write_m3u(channels: list[tuple[str, str]], path: Path):
    """Write channels to M3U file."""
    lines = ['#EXTM3U']
    for name, url in channels:
        lines.append(f'#EXTINF:-1,{name}')
        lines.append(url)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("Wrote %d channels to %s", len(channels), path)


async def main():
    log.info("Stream4Free scraper — nodriver + Xvfb")
    log.info("Python %s on %s", sys.version, sys.platform)

    channels = await scrape_stream4free()

    if channels:
        write_m3u(channels, M3U_FILE)
        log.info("SUCCESS: %d channels written", len(channels))
    else:
        log.warning("No channels found — keeping existing M3U file")
        # Safety: don't overwrite with empty file
        if M3U_FILE.exists():
            lines = M3U_FILE.read_text(encoding="utf-8").strip().split("\n")
            existing = sum(1 for l in lines if l.startswith("#EXTINF"))
            log.info("Existing M3U has %d channels (preserved)", existing)

    return len(channels)


if __name__ == "__main__":
    count = asyncio.run(main())
    sys.exit(0 if count > 0 else 1)
