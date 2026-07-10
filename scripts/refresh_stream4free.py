#!/usr/bin/env python3
"""
Stream4Free scraper â nodriver (CDP direct) + Xvfb virtual display.

CF Turnstile bypass via genuine Chrome (non-headless) on a virtual display.
nodriver = official successor of undetected-chromedriver, communicates via CDP,
no Selenium/WebDriver detection vectors.

Key insight: CF detects headless mode â we run headful Chrome on Xvfb.
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
CF_WAIT_AFTER_VERIFY = 8       # seconds to wait after cf_verify()
PAGE_LOAD_WAIT = 5             # seconds to wait for JS rendering
CF_INDICATORS = [
    "just a moment",
    "checking your browser",
    "turnstile",
    "cf-challenge",
    "cf_clearance",
    "security check",
    "please wait",
    "vÃ©rification",
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
        log.info("Not Linux â skipping virtual display setup")
        return None
    # Check if a real display is available
    if os.environ.get("DISPLAY"):
        log.info("DISPLAY already set (%s) â skipping Xvfb", os.environ["DISPLAY"])
        return None
    try:
        from pyvirtualdisplay import Display
        display = Display(visible=False, size=(1920, 1080))
        display.start()
        log.info("Virtual display started (Xvfb) â DISPLAY=%s", os.environ.get("DISPLAY"))
        return display
    except Exception as e:
        log.warning("Failed to start virtual display: %s â trying headless fallback", e)
        return None


# ---------------------------------------------------------------------------
# Core: nodriver CF bypass + page fetch
# ---------------------------------------------------------------------------
async def fetch_page_with_nodriver(url: str, headless: bool = False) -> str | None:
    """
    Open *url* in genuine Chrome via nodriver, handle CF Turnstile, return HTML.
    headless=False is CRITICAL â CF detects headless mode.
    """
    import nodriver as uc

    browser_args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
        "--disable-blink-features=AutomationControlled",
        "--disable-extensions",
        "--disable-popup-blocking",
        "--disable-infobars",
        "--lang=fr-FR",
    ]

    log.info("Launching Chrome via nodriver (headless=%s)", headless)
    browser = await uc.start(
        headless=headless,
        browser_args=browser_args,
        lang="fr-FR",
    )

    try:
        log.info("Navigating to %s", url)
        tab = await browser.get(url)
        await asyncio.sleep(PAGE_LOAD_WAIT)

        # Check for CF challenge
        page_content = await tab.get_content()
        page_lower = page_content.lower() if page_content else ""
        cf_detected = any(ind in page_lower for ind in CF_INDICATORS)

        if cf_detected:
            log.info("CF challenge detected â calling cf_verify()")
            try:
                await tab.cf_verify()
                log.info("cf_verify() returned â waiting %ds for page to load", CF_WAIT_AFTER_VERIFY)
                await asyncio.sleep(CF_WAIT_AFTER_VERIFY)
            except Exception as e:
                log.warning("cf_verify() raised: %s â waiting anyway", e)
                await asyncio.sleep(CF_WAIT_AFTER_VERIFY)

            # Re-check: sometimes we need to navigate again after CF clears
            page_content = await tab.get_content()
            page_lower = page_content.lower() if page_content else ""

            # If still on CF page, try navigating again (cookies should be set now)
            if any(ind in page_lower for ind in ["just a moment", "checking your browser", "cf-challenge"]):
                log.info("Still on CF page â re-navigating with cookies")
                tab = await browser.get(url)
                await asyncio.sleep(PAGE_LOAD_WAIT)
                page_content = await tab.get_content()
        else:
            log.info("No CF challenge detected â page loaded directly")

        # Check for "enable JavaScript" gate (2nd layer)
        if page_content and "enable javascript" in page_content.lower():
            log.info("JavaScript gate detected â waiting extra 5s for JS render")
            await asyncio.sleep(5)
            page_content = await tab.get_content()

        content_len = len(page_content) if page_content else 0
        log.info("Page content length: %d chars", content_len)

        # Quick sanity: real page should be > 5KB
        if content_len < 2000:
            log.warning("Page content suspiciously short (%d chars) â might still be on CF", content_len)
            # One more wait + get
            await asyncio.sleep(5)
            page_content = await tab.get_content()
            content_len = len(page_content) if page_content else 0
            log.info("After extra wait: %d chars", content_len)

        return page_content

    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def fetch_channel_stream(browser_tab, channel_url: str) -> list[str]:
    """Navigate to a channel page and extract stream URLs."""
    try:
        tab = await browser_tab.get(channel_url)
        await asyncio.sleep(3)
        content = await tab.get_content()
        if content:
            return extract_stream_urls(content)
    except Exception as e:
        log.warning("Error fetching channel %s: %s", channel_url, e)
    return []


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
        log.warning("No virtual display available â falling back to headless (less reliable)")

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
            html_lower = html.lower()
            if "just a moment" in html_lower or "cf-challenge" in html_lower:
                log.warning("Attempt %d: still on CF challenge page", attempt)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(5)
                continue

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
            # (some sites embed streams directly)
            direct_streams = extract_stream_urls(html)
            if direct_streams:
                log.info("Found %d direct stream URLs on listing page", len(direct_streams))

            # For now, log what we found â the channel detail scraping
            # can be added once listing works
            log.info("Listing page HTML snippet (first 500 chars):\n%s", html[:500])

            # If we got a real page, we're done with CF bypass
            if len(html) > 5000:
                log.info("Got substantial page content (%d chars) â CF bypass SUCCESS", len(html))

                # Parse the full page for m3u8 links in any form
                all_m3u8 = re.findall(
                    r'(https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*)', html, re.IGNORECASE
                )
                log.info("Total m3u8 URLs found in page: %d", len(all_m3u8))
                for u in all_m3u8[:5]:
                    log.info("  m3u8: %s", u)

                # The actual channel extraction logic depends on the page structure
                # which we'll see once CF is bypassed. For now return what we have.
                # TODO: refine extraction after seeing real page structure
                break
            else:
                log.warning("Page content only %d chars â may be incomplete", len(html))

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
    log.info("Stream4Free scraper â nodriver + Xvfb")
    log.info("Python %s on %s", sys.version, sys.platform)

    channels = await scrape_stream4free()

    if channels:
        write_m3u(channels, M3U_FILE)
        log.info("SUCCESS: %d channels written", len(channels))
    else:
        log.warning("No channels found â keeping existing M3U file")
        # Safety: don't overwrite with empty file
        if M3U_FILE.exists():
            lines = M3U_FILE.read_text(encoding="utf-8").strip().split("\n")
            existing = sum(1 for l in lines if l.startswith("#EXTINF"))
            log.info("Existing M3U has %d channels (preserved)", existing)

    return len(channels)


if __name__ == "__main__":
    count = asyncio.run(main())
    sys.exit(0 if count > 0 else 1)
#!/usr/bin/env python3
"""
refresh_stream4free.py - Scraper pour stream4free.tv
Utilise Scrapling StealthyFetcher (Patchright/Chromium furtif)
avec solve_cloudflare=True pour bypasser CF Turnstile nativement.
"""
import os, sys, time

PAGE_URL = "https://www.stream4free.tv/tv-live-france"
BASE_URL = "https://www.stream4free.tv"
OUT_FILE = "data-stream4free.m3u"
GROUP = "Stream4Free"

TITLE_CLEAN = [" - Stream4Free", " | Stream4Free", " en streaming gratuit",
               " en direct gratuit", " en streaming", " en direct",
               " Live Streaming", " Stream", " Live"]
SKIP_SLUGS = {"tv-live-france", "tv-show-series", "privacy-policy",
              "register-account", "forum", "#"}
CF_TITLES = ["un instant", "just a moment", "checking", "attention required",
             "please wait", "verify"]

def clean_title(raw, slug):
    t = raw.strip()
    for s in TITLE_CLEAN:
        if t.lower().endswith(s.lower()):
            t = t[:-len(s)].strip()
    if not t:
        t = slug.replace("-", " ").title()
    return t

def is_cf_challenge(title):
    low = (title or "").lower()
    return any(k in low for k in CF_TITLES)

def extract_channels(page):
    """Extract channel entries from a Scrapling Response."""
    entries = []
    seen = set()
    for link in page.css('a'):
        try:
            href = link.attrib.get('href', '')
            if not href or href == '#':
                continue
            if href.startswith('/'):
                slug = href.strip('/').split('/')[-1]
            elif href.startswith(BASE_URL):
                slug = href.replace(BASE_URL, '').strip('/').split('/')[-1]
            else:
                continue
            if not slug or slug in SKIP_SLUGS or slug in seen:
                continue
            if slug.startswith('category') or slug.startswith('tag'):
                continue
            imgs = link.css('img')
            raw_title = ""
            logo = ""
            if imgs:
                raw_title = imgs[0].attrib.get('alt', '')
                logo = imgs[0].attrib.get('src', '') or imgs[0].attrib.get('data-src', '')
            if not raw_title:
                raw_title = link.get_all_text(strip=True)
            title_clean = clean_title(raw_title, slug)
            if not title_clean or len(title_clean) < 2:
                continue
            seen.add(slug)
            entries.append({"slug": slug, "title": title_clean, "logo": logo})
        except Exception:
            continue
    return entries

def run_scraper():
    from scrapling.fetchers import StealthyFetcher
    entries = []
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        print(f"Tentative {attempt}/{max_retries} (Scrapling StealthyFetcher)...")
        try:
            page = StealthyFetcher.fetch(
                PAGE_URL,
                headless=True,
                network_idle=True,
                solve_cloudflare=True,
                timeout=120000,
                wait=3000,
                locale="fr-FR",
                timezone_id="Europe/Paris",
                hide_canvas=True,
                block_webrtc=True,
                dns_over_https=True,
            )
            title = page.css('title::text').get() or ""
            print(f"  Page title: {title}")
            if is_cf_challenge(title):
                print(f"  WARN: CF challenge still active after solve_cloudflare")
                if attempt < max_retries:
                    time.sleep(10)
                continue
            entries = extract_channels(page)
            if entries:
                print(f"  OK: {len(entries)} chaines")
                break
            else:
                print("  0 chaines via liens generiques, essai pbitem_cont...")
                seen = set()
                for el in page.css('a.pbitem_cont'):
                    try:
                        href = el.attrib.get('href', '')
                        if not href or href == '#':
                            continue
                        if href.startswith('/'):
                            slug = href.strip('/').split('/')[-1]
                        elif href.startswith(BASE_URL):
                            slug = href.replace(BASE_URL, '').strip('/').split('/')[-1]
                        else:
                            continue
                        if not slug or slug in SKIP_SLUGS or slug in seen:
                            continue
                        imgs = el.css('img')
                        raw_title = imgs[0].attrib.get('alt', '') if imgs else el.get_all_text(strip=True)
                        logo = imgs[0].attrib.get('src', '') if imgs else ""
                        title_clean = clean_title(raw_title, slug)
                        if not title_clean or len(title_clean) < 2:
                            continue
                        seen.add(slug)
                        entries.append({"slug": slug, "title": title_clean, "logo": logo})
                    except Exception:
                        continue
                if entries:
                    print(f"  OK (pbitem_cont): {len(entries)} chaines")
                    break
                if attempt < max_retries:
                    time.sleep(10)
        except Exception as e:
            print(f"  ERR {attempt}: {e}")
            import traceback
            traceback.print_exc()
            if attempt < max_retries:
                time.sleep(10)
    return entries

def main():
    entries = run_scraper()
    print(f"\nResultat: {len(entries)} chaines")
    if not entries:
        if os.path.exists(OUT_FILE):
            print("WARN: 0 items, on GARDE l'ancien fichier")
            sys.exit(0)
        else:
            print("ERR: 0 items et pas d'ancien fichier")
            sys.exit(1)
    lines = ["#EXTM3U"]
    for e in entries:
        extinf = f'#EXTINF:-1 group-title="{GROUP}"'
        if e["logo"]:
            extinf += f' tvg-logo="{e["logo"]}"'
        extinf += f', {e["title"]}'
        lines.append(extinf)
        lines.append(f'stream4free://{e["slug"]}')
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"OK: {len(entries)} chaines ecrites dans {OUT_FILE}")

if __name__ == "__main__":
    main()
