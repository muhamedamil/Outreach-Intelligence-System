# app/services/scraper/website_analyzer.py
#
# "Top 1%" Website Intelligence Engine — Production Grade
#
# HANDLES:
#   1. Deep booking system detection (JS-rendered iframes, 5 layers)
#   2. WhatsApp signal detection (website scan + phone number verification)
#   3. Social media extraction (5 methods: links, footer, JSON-LD, meta, icons)
#   4. Website status detection (dead, parked, under construction, cloudflare)
#   5. Website tech quality scoring (legacy vs modern)
#   6. Phone number backup extraction
#   7. Cookie consent auto-dismissal
#
# PRODUCTION EDGE CASES:
#   - Dead domains (DNS failure) → NO_WEBSITE
#   - Parked domains (GoDaddy/Sedo) → NO_WEBSITE
#   - Under construction pages → NO_WEBSITE + highest lead score
#   - Cloudflare challenges → STATIC_WEBSITE (basic hosting)
#   - Redirect loops → graceful timeout
#   - Cookie consent banners → auto-dismiss before scanning
#   - Timeout sites → fallback to domcontentloaded

import re
import json
import asyncio
import logging
import sys
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from playwright.async_api import async_playwright, Page, Browser
from app.models.lead import LeadProfile, LeadCategory, WhatsAppStatus, WebsiteStatus
from app.services.scraper.social_resolver import resolve_missing_socials

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# WINDOWS PLAYWRIGHT ISOLATION
# ─────────────────────────────────────────────
# Uvicorn --reload on Windows forces SelectorEventLoop, which
# does NOT support asyncio.create_subprocess_exec() (needed by
# Playwright to launch browsers). This helper detects that
# situation and offloads to a thread with its own ProactorEventLoop.

_pw_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="pw")


def _needs_thread_isolation() -> bool:
    """Check if we need to offload Playwright to a separate thread."""
    if sys.platform != "win32":
        return False
    try:
        loop = asyncio.get_running_loop()
        # ProactorEventLoop supports subprocesses, SelectorEventLoop does not
        return not isinstance(loop, asyncio.ProactorEventLoop)
    except RuntimeError:
        return False


def _run_coro_in_proactor(coro_func, *args):
    """Run an async function in a fresh ProactorEventLoop (called from thread)."""
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro_func(*args))
    finally:
        loop.close()


async def _safe_playwright_call(coro_func, *args):
    """
    Execute a Playwright-dependent coroutine safely on Windows.
    If the current loop is SelectorEventLoop (from uvicorn --reload),
    offload to a dedicated thread with a ProactorEventLoop.
    """
    if _needs_thread_isolation():
        logger.debug(f"Offloading Playwright task to ProactorEventLoop thread")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _pw_executor, _run_coro_in_proactor, coro_func, *args
        )
    else:
        return await coro_func(*args)


# ─────────────────────────────────────────────
# SIGNATURE DATABASES
# ─────────────────────────────────────────────

BOOKING_PLATFORMS = {
    # Salon Automation Tools
    "Vagaro": {"urls": ["vagaro.com"], "buttons": ["book with vagaro"]},
    "Mindbody": {
        "urls": ["mindbodyonline.com", "mndbdy.ly", "mindbody.io"],
        "buttons": ["book via mindbody"],
    },
    "Fresha": {"urls": ["fresha.com", "shedul.com"], "buttons": ["book on fresha"]},
    "Calendly": {"urls": ["calendly.com"], "buttons": []},
    "Acuity": {
        "urls": ["acuityscheduling.com", "squareup.com/appointments"],
        "buttons": ["schedule with acuity"],
    },
    "Phorest": {"urls": ["phorest.com"], "buttons": []},
    "GlossGenius": {"urls": ["glossgenius.com"], "buttons": []},
    "Boulevard": {"urls": ["joinblvd.com", "boulevard.io"], "buttons": []},
    "Booksy": {"urls": ["booksy.com"], "buttons": ["book with booksy"]},
    "StyleSeat": {"urls": ["styleseat.com"], "buttons": []},
    "Square": {"urls": ["squareup.com/appointments", "square.site"], "buttons": []},
    "Setmore": {"urls": ["setmore.com"], "buttons": []},
    "SimplyBook": {"urls": ["simplybook.me"], "buttons": []},
    # Solar Automation Tools (Calculators & Quoters)
    "Aurora": {
        "urls": ["aurorasolar.com"],
        "buttons": ["get a quote", "estimate savings", "solar quote"],
    },
    "Solargraf": {"urls": ["solargraf.com"], "buttons": []},
    "OpenSolar": {"urls": ["opensolar.com"], "buttons": []},
    "EnergySage": {"urls": ["energysage.com"], "buttons": []},
    "GenericSolar": {
        "urls": [],
        "buttons": ["solar calculator", "calculate solar", "get free estimate"],
    },
}

# WhatsApp detection patterns
WHATSAPP_PATTERNS = [
    "wa.me",
    "api.whatsapp.com",
    "whatsapp.com/send",
    "chat.whatsapp.com",
]

# Phone number regex (US format)
US_PHONE_REGEX = re.compile(r"(\+?1?\s*[-.\s]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})")

# ── EDGE CASE: Parked domain signatures ──
PARKED_DOMAIN_SIGNATURES = [
    "this domain is for sale",
    "domain is parked",
    "buy this domain",
    "godaddy",
    "sedo.com",
    "dan.com",
    "hugedomains.com",
    "afternic.com",
    "this webpage is parked",
    "domain parking",
    "is available for purchase",
]

# ── EDGE CASE: Under construction signatures ──
UNDER_CONSTRUCTION_SIGNATURES = [
    "coming soon",
    "under construction",
    "website is under maintenance",
    "we're working on it",
    "launching soon",
    "site is being built",
    "stay tuned",
    "we'll be right back",
    "check back soon",
    "page is under construction",
]

# ── EDGE CASE: Cloudflare / bot challenge signatures ──
CLOUDFLARE_SIGNATURES = [
    "just a moment",
    "checking your browser",
    "enable javascript and cookies",
    "ray id",
    "cloudflare",
    "attention required",
    "ddos protection by",
    "please wait while we verify",
]

# Legacy / Modern tech indicators
LEGACY_SIGNATURES = [
    ".php",
    ".asp",
    ".aspx",
    ".cfm",
    ".jsp",
    "wordpress",
    "wp-content",
    "wp-includes",
    "wix.com",
    "weebly.com",
    "godaddy.com/website",
    "jimdo",
]

MODERN_SIGNATURES = [
    "next/",
    "_next/",
    "nuxt",
    "__nuxt",
    "react",
    "vue",
    "angular",
    "vercel",
    "netlify",
]

# Subpages to check for booking (and social media)
BOOKING_SUBPAGES = ["/book", "/booking", "/appointments", "/schedule", "/services"]
SOCIAL_SUBPAGES = ["/about", "/contact", "/about-us", "/contact-us"]

# ── DEEP SOCIAL MEDIA: All patterns ──
SOCIAL_LINK_PATTERNS = {
    "instagram": {
        "domains": ["instagram.com"],
        "regex": r"(?:https?://)?(?:www\.)?instagram\.com/([a-zA-Z0-9_.]+)/?",
        "exclude": [
            "instagram.com/explore",
            "instagram.com/accounts",
            "instagram.com/p/",
            "instagram.com/reel/",
            "instagram.com/stories/",
        ],
    },
    "facebook": {
        "domains": ["facebook.com", "fb.com"],
        "regex": r"(?:https?://)?(?:www\.)?(?:facebook|fb)\.com/([a-zA-Z0-9_.]+)/?",
        "exclude": [
            "facebook.com/sharer",
            "facebook.com/share",
            "facebook.com/dialog",
            "facebook.com/plugins",
            "facebook.com/tr",
        ],
    },
    "tiktok": {
        "domains": ["tiktok.com"],
        "regex": r"(?:https?://)?(?:www\.)?tiktok\.com/@([a-zA-Z0-9_.]+)/?",
        "exclude": [],
    },
    "youtube": {
        "domains": ["youtube.com", "youtu.be"],
        "regex": r"(?:https?://)?(?:www\.)?youtube\.com/(?:channel/|c/|@)([a-zA-Z0-9_-]+)/?",
        "exclude": ["youtube.com/watch", "youtube.com/embed"],
    },
    "yelp": {
        "domains": ["yelp.com"],
        "regex": r"(?:https?://)?(?:www\.)?yelp\.com/biz/([a-zA-Z0-9_-]+)/?",
        "exclude": [],
    },
}

# Cookie consent button selectors (auto-dismiss)
COOKIE_DISMISS_SELECTORS = [
    "button[id*='accept']",
    "button[class*='accept']",
    "button[id*='cookie']",
    "button[class*='cookie']",
    "button[id*='consent']",
    "button[class*='consent']",
    "a[id*='accept']",
    "a[class*='accept']",
    "[data-testid='cookie-accept']",
    ".cookie-banner button",
    "#cookie-notice button",
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Got it')",
    "button:has-text('OK')",
]


# ─────────────────────────────────────────────
# CORE ANALYSIS ENGINE
# ─────────────────────────────────────────────

# Playwright error strings that indicate a JS-driven page navigation
# destroyed the current execution context mid-analysis.
_NAVIGATION_ERROR_SIGNALS = (
    "execution context was destroyed",
    "target page, context or browser has been closed",
    "page has been closed",
    "target closed",
    "session closed",
    "browser has been disconnected",
)


def _is_navigation_error(err_msg: str) -> bool:
    """Return True if the error is caused by a mid-analysis JS navigation."""
    low = err_msg.lower()
    return any(sig in low for sig in _NAVIGATION_ERROR_SIGNALS)


async def _try_recover_after_navigation(page, lead: LeadProfile) -> bool:
    """
    After a navigation interruption is detected, wait for the new page to
    settle and try to classify the lead based on its final URL.

    Returns True if we were able to make a classification decision
    (caller should return lead immediately). Returns False if the new
    page is a regular site worth continuing to analyse.
    """
    try:
        # Give the new page up to 5 seconds to finish loading.
        await asyncio.wait_for(
            page.wait_for_load_state("domcontentloaded"), timeout=5
        )
    except Exception:
        pass  # Even a partial load is useful

    new_url = page.url
    logger.info(f"[{lead.name}] Post-navigation URL: {new_url}")

    # Did we land on a booking platform?
    platform = _is_booking_platform_url(new_url)
    if platform:
        lead.category = LeadCategory.FULLY_AUTOMATED
        lead.booking_system = platform
        lead.website_status = WebsiteStatus.LIVE
        logger.info(f"[{lead.name}] 🔀 Redirect resolved → {platform} (FULLY_AUTOMATED)")
        return True

    # Did we land on a social media page?
    if _is_social_media_url(new_url):
        lead.category = LeadCategory.NO_WEBSITE
        lead.website_status = WebsiteStatus.NONE
        lead.lead_score += 15
        lead.lead_score_breakdown["social_redirect"] = 15
        _extract_social_from_url(new_url, lead)
        logger.info(f"[{lead.name}] 🔀 Redirect resolved → social media (NO_WEBSITE)")
        return True

    # Regular page redirect — we have partial data, caller keeps going
    logger.info(f"[{lead.name}] 🔀 Mid-analysis redirect to regular page; using partial data.")
    lead.website_status = WebsiteStatus.LIVE
    if lead.category == LeadCategory.NO_WEBSITE:
        lead.category = LeadCategory.STATIC_WEBSITE
    return False


async def analyze_lead_website(lead: LeadProfile) -> LeadProfile:
    """
    Deep website intelligence gathering using Playwright.
    Enriches a LeadProfile already populated by Apify.

    Hard 60-second total budget: if the browser stalls for any reason
    (network hang, renderer crash, infinite JS loop) we abort cleanly
    and return whatever partial data we collected.
    """

    # ── Already classified by Apify as automated ──
    if lead.category == LeadCategory.FULLY_AUTOMATED and lead.booking_system:
        logger.info(
            f"[{lead.name}] Already FULLY_AUTOMATED ({lead.booking_system}). Skipping crawl."
        )
        return lead

    # ── No website URL ──
    if not lead.website_url:
        lead.category = LeadCategory.NO_WEBSITE
        lead.website_status = WebsiteStatus.NONE
        lead.lead_score += 10
        lead.lead_score_breakdown["no_website_bonus"] = 10
        logger.info(f"[{lead.name}] No website URL. Category: NO_WEBSITE")
        return lead

    url = str(lead.website_url)

    # ── "Website" is a social media page ──
    if _is_social_media_url(url):
        lead.category = LeadCategory.NO_WEBSITE
        lead.website_status = WebsiteStatus.NONE
        lead.lead_score += 15
        lead.lead_score_breakdown["social_as_website"] = 15
        _extract_social_from_url(url, lead)
        logger.info(
            f"[{lead.name}] Website is social media ({url}). Category: NO_WEBSITE"
        )
        return lead

    # ── "Website" IS a booking platform ──
    platform = _is_booking_platform_url(url)
    if platform:
        lead.category = LeadCategory.FULLY_AUTOMATED
        lead.booking_system = platform
        lead.website_status = WebsiteStatus.LIVE
        logger.info(f"[{lead.name}] Website IS {platform}. Category: FULLY_AUTOMATED")
        return lead

    # ── FULL BROWSER ANALYSIS — hard 75s total budget ──
    logger.info(f"[{lead.name}] Starting browser analysis: {url}")
    try:
        return await asyncio.wait_for(
            _safe_playwright_call(_browser_analyze_lead, lead, url),
            timeout=75,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"[{lead.name}] ⏱ Analysis budget exceeded (75s). Returning partial data."
        )
        lead.website_status = WebsiteStatus.TIMEOUT
        if lead.category == LeadCategory.NO_WEBSITE:
            lead.category = LeadCategory.STATIC_WEBSITE
        return lead


async def _browser_analyze_lead(lead: LeadProfile, url: str) -> LeadProfile:
    """
    Inner function that does the actual Playwright browser work.

    NAVIGATION HANDLING ARCHITECTURE:
    Every analysis step is wrapped individually. When a JS redirect fires
    mid-step (destroying the execution context), we detect it immediately,
    stop wasting time on the dead context, wait for the new page to settle,
    and try to classify the new URL before returning whatever we collected.
    """
    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                ignore_https_errors=True,
            )
            page = await context.new_page()

            # ── PHASE 1: INITIAL NAVIGATION ──
            response = None
            try:
                # Reduced initial timeout from 25s to 18s to prevent total budget consumption
                response = await page.goto(url, wait_until="load", timeout=18000)
                # Let the network settle (catches late JS redirects) - reduced timeout
                await page.wait_for_load_state("networkidle", timeout=5000)
                await asyncio.sleep(1.0)
            except Exception as nav_err:
                error_msg = str(nav_err).lower()

                # Dead site — DNS / connection refused
                if (
                    "net::err_name_not_resolved" in error_msg
                    or "net::err_connection_refused" in error_msg
                    or "net::err_internet_disconnected" in error_msg
                ):
                    lead.category = LeadCategory.NO_WEBSITE
                    lead.website_status = WebsiteStatus.DEAD
                    lead.lead_score += 20
                    lead.lead_score_breakdown["dead_website"] = 20
                    logger.info(f"[{lead.name}] DEAD (DNS/connection failure)")
                    await browser.close()
                    return lead

                # Timeout — site exists but too slow
                if "timeout" in error_msg:
                    lead.website_status = WebsiteStatus.TIMEOUT
                    lead.category = LeadCategory.STATIC_WEBSITE
                    lead.lead_score += 10
                    lead.lead_score_breakdown["slow_website"] = 10
                    logger.info(f"[{lead.name}] TIMEOUT on initial load")
                    await browser.close()
                    return lead

                # Mid-navigation redirect (JS redirect fired during goto)
                if _is_navigation_error(error_msg):
                    classified = await _try_recover_after_navigation(page, lead)
                    await browser.close()
                    return lead

                # Any other unrecoverable navigation error
                lead.website_status = WebsiteStatus.ERROR
                logger.warning(f"[{lead.name}] Navigation error: {nav_err}")
                await browser.close()
                return lead

            # ── PHASE 2: POST-LOAD CLASSIFICATION ──

            # Immediate redirect to booking platform?
            final_url = page.url
            redirect_platform = _is_booking_platform_url(final_url)
            if redirect_platform:
                lead.category = LeadCategory.FULLY_AUTOMATED
                lead.booking_system = redirect_platform
                lead.website_status = WebsiteStatus.LIVE
                logger.info(f"[{lead.name}] Redirected → {redirect_platform}")
                await browser.close()
                return lead

            # HTTP errors — distinguish bot-protection (403) from real errors
            if response and response.status >= 400:
                if response.status == 403:
                    # 403 = bot protection (Cloudflare, Akamai, nginx auth)
                    # The site EXISTS and is likely STATIC — don't kill the lead
                    lead.category = LeadCategory.STATIC_WEBSITE
                    lead.website_status = WebsiteStatus.CLOUDFLARE_BLOCKED
                    lead.lead_score += 5
                    lead.lead_score_breakdown["bot_protection_403"] = 5
                    logger.warning(f"[{lead.name}] HTTP 403 — bot protection, treating as STATIC")
                elif response.status == 404:
                    lead.category = LeadCategory.NO_WEBSITE
                    lead.website_status = WebsiteStatus.ERROR
                    logger.warning(f"[{lead.name}] HTTP 404 — page not found")
                else:
                    lead.category = LeadCategory.NO_WEBSITE
                    lead.website_status = WebsiteStatus.ERROR
                    lead.lead_score += 10
                    lead.lead_score_breakdown["broken_website"] = 10
                    logger.warning(f"[{lead.name}] HTTP {response.status}")
                await browser.close()
                return lead

            # ── PHASE 3: CONTENT ANALYSIS ──

            # Grab page source — wrap in try so a navigation here doesn't kill us
            page_html = ""
            page_text = ""
            try:
                page_html = await page.content()
                page_text = await page.inner_text("body")
            except Exception as e:
                if _is_navigation_error(str(e)):
                    logger.warning(f"[{lead.name}] Navigation during content read — recovering")
                    classified = await _try_recover_after_navigation(page, lead)
                    await browser.close()
                    return lead
                # Partial content is still useful — continue

            page_html_lower = page_html.lower()
            page_text_lower = page_text.lower()

            # Parked / under construction / Cloudflare — fast-exit checks
            if _is_parked_domain(page_text_lower, page_html_lower):
                lead.category = LeadCategory.NO_WEBSITE
                lead.website_status = WebsiteStatus.PARKED
                lead.lead_score += 20
                lead.lead_score_breakdown["parked_domain"] = 20
                logger.info(f"[{lead.name}] PARKED domain")
                await browser.close()
                return lead

            if _is_under_construction(page_text_lower, page_html_lower):
                lead.category = LeadCategory.NO_WEBSITE
                lead.website_status = WebsiteStatus.UNDER_CONSTRUCTION
                lead.lead_score += 25
                lead.lead_score_breakdown["under_construction"] = 25
                logger.info(f"[{lead.name}] UNDER CONSTRUCTION")
                await browser.close()
                return lead

            if _is_cloudflare_blocked(page_text_lower, page_html_lower):
                lead.category = LeadCategory.STATIC_WEBSITE
                lead.website_status = WebsiteStatus.CLOUDFLARE_BLOCKED
                lead.lead_score += 5
                lead.lead_score_breakdown["cloudflare_basic"] = 5
                logger.info(f"[{lead.name}] Cloudflare/bot challenge")
                await browser.close()
                return lead

            lead.website_status = WebsiteStatus.LIVE

            # ── STEP 0: Cookie consent dismissal ──
            try:
                await _dismiss_cookie_consent(page)
            except Exception as e:
                if _is_navigation_error(str(e)):
                    classified = await _try_recover_after_navigation(page, lead)
                    await browser.close()
                    return lead

            # ── STEP 1: Booking system detection ──
            # Independent try/except — navigation here means the site itself
            # redirected to a booking platform when a button was clicked.
            try:
                booking = await _detect_booking_system(page, page_html)
            except Exception as e:
                booking = None
                if _is_navigation_error(str(e)):
                    logger.info(f"[{lead.name}] Navigation during booking scan — checking new URL")
                    classified = await _try_recover_after_navigation(page, lead)
                    if classified:
                        await browser.close()
                        return lead

            if booking:
                lead.category = LeadCategory.FULLY_AUTOMATED
                lead.booking_system = booking
                logger.info(f"[{lead.name}] Booking: {booking}")
            else:
                try:
                    # Pass context instead of page so the homepage DOM remains intact
                    booking_sub = await _check_subpages_for_booking(context, url)
                except Exception as e:
                    booking_sub = None
                    if _is_navigation_error(str(e)):
                        classified = await _try_recover_after_navigation(page, lead)
                        if classified:
                            await browser.close()
                            return lead

                if booking_sub:
                    lead.category = LeadCategory.FULLY_AUTOMATED
                    lead.booking_system = booking_sub
                    logger.info(f"[{lead.name}] Booking (subpage): {booking_sub}")
                else:
                    lead.category = LeadCategory.STATIC_WEBSITE
                    lead.lead_score += 15
                    lead.lead_score_breakdown["static_website"] = 15
                    logger.info(f"[{lead.name}] STATIC_WEBSITE")

            # ── STEP 2: WhatsApp signal from page ──
            try:
                await _detect_whatsapp_from_page(page, page_html, lead)
            except Exception as e:
                if _is_navigation_error(str(e)):
                    logger.debug(f"[{lead.name}] Navigation during WhatsApp scan — skipping")
                # Non-navigation error: silently skip, don't kill the whole pipeline

            # ── STEP 3: Phone backup from page text ──
            if not lead.phone and not lead.phone_unformatted:
                _extract_phone_from_text(page_text, lead)

            # ── STEP 4: Deep social media extraction ──
            try:
                await _deep_extract_social_media(page, page_html, lead)
            except Exception as e:
                if _is_navigation_error(str(e)):
                    logger.debug(f"[{lead.name}] Navigation during social scan — using partial data")

            # ── STEP 5: Website quality scoring (sync — cannot cause navigation) ──
            _score_website_quality(page_html_lower, lead)

            # ── STEP 6: OSINT social resolver (network, not Playwright) ──
            if not lead.instagram_url or not lead.facebook_url:
                try:
                    await resolve_missing_socials(lead)
                except Exception as e:
                    logger.warning(f"[{lead.name}] OSINT resolver error: {e}")

            lead.lead_score = max(0, min(100, lead.lead_score))
            await browser.close()
            return lead

        except Exception as e:
            err_msg = str(e)
            if _is_navigation_error(err_msg):
                logger.warning(
                    f"[{lead.name}] ⚡ Unexpected navigation escape — returning partial data"
                )
            else:
                logger.error(f"[{lead.name}] Browser analysis failed: {err_msg}")

            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            lead.lead_score = max(0, min(100, lead.lead_score))
            return lead


# ─────────────────────────────────────────────
# BOOKING DETECTION (Multi-Layer)
# ─────────────────────────────────────────────


async def _detect_booking_system(page: Page, html: str) -> Optional[str]:
    """5-layer booking detection."""

    # Layer 1: Iframes
    iframes = await page.query_selector_all("iframe")
    for iframe in iframes:
        src = await iframe.get_attribute("src") or ""
        for platform, data in BOOKING_PLATFORMS.items():
            if any(sig in src.lower() for sig in data["urls"]):
                return platform

    # Layer 2: Links
    links = await page.query_selector_all("a[href]")
    for link in links:
        href = await link.get_attribute("href") or ""
        for platform, data in BOOKING_PLATFORMS.items():
            if any(sig in href.lower() for sig in data["urls"]):
                return platform

    # Layer 3: Script tags
    scripts = await page.query_selector_all("script[src]")
    for script in scripts:
        src = await script.get_attribute("src") or ""
        for platform, data in BOOKING_PLATFORMS.items():
            if any(sig in src.lower() for sig in data["urls"]):
                return platform

    # Layer 4: Button text
    buttons = await page.query_selector_all("a, button")
    for btn in buttons:
        try:
            text = (await btn.inner_text()).lower().strip()
            for platform, data in BOOKING_PLATFORMS.items():
                if any(btn_text in text for btn_text in data["buttons"] if btn_text):
                    return platform
        except Exception:
            continue

    # Layer 5: Raw HTML
    html_lower = html.lower()
    for platform, data in BOOKING_PLATFORMS.items():
        if any(sig in html_lower for sig in data["urls"]):
            return platform

    return None


async def _check_subpages_for_booking(context, url: str) -> Optional[str]:
    """Check common subpages for booking systems without hijacking the main page."""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    for subpage in BOOKING_SUBPAGES[:3]:
        sub_page = await context.new_page()
        try:
            sub_url = f"{base}{subpage}"
            response = await sub_page.goto(
                sub_url, wait_until="domcontentloaded", timeout=4500
            )
            if response and response.status < 400:
                redirect_platform = _is_booking_platform_url(sub_page.url)
                if redirect_platform:
                    return redirect_platform
                sub_html = await sub_page.content()
                html_lower = sub_html.lower()
                for platform, data in BOOKING_PLATFORMS.items():
                    if any(sig in html_lower for sig in data["urls"]):
                        return platform
        except Exception:
            continue
        finally:
            await sub_page.close()
    return None


# ─────────────────────────────────────────────
# WHATSAPP FROM WEBSITE (page scan)
# ─────────────────────────────────────────────


async def _detect_whatsapp_from_page(page: Page, html: str, lead: LeadProfile):
    """Detect WhatsApp from website content (links, widgets, HTML)."""

    # Signal 1: Direct links
    for pattern in WHATSAPP_PATTERNS:
        selector = f"a[href*='{pattern}']"
        wa_element = await page.query_selector(selector)
        if wa_element:
            href = await wa_element.get_attribute("href") or ""
            number = _extract_number_from_wa_link(href)
            if number:
                lead.whatsapp_status = WhatsAppStatus.DETECTED
                lead.whatsapp_number = number
                lead.lead_score += 20
                lead.lead_score_breakdown["whatsapp_verified"] = 20
                logger.info(f"[{lead.name}] WhatsApp link found: {number}")
                return

    # Signal 2: Chat widgets
    chat_selectors = [
        "[class*='whatsapp']",
        "[id*='whatsapp']",
        "[class*='wa-chat']",
        "[data-action='whatsapp']",
        "[class*='chaty-widget']",
        "[class*='elfsight-app']",
        "div[class*='wh-widget']",
        "[class*='whatshelp']",
        "[class*='joinchat']",
    ]
    for selector in chat_selectors:
        widget = await page.query_selector(selector)
        if widget:
            lead.whatsapp_status = WhatsAppStatus.DETECTED
            lead.lead_score += 15
            lead.lead_score_breakdown["whatsapp_widget"] = 15
            logger.info(f"[{lead.name}] WhatsApp widget: {selector}")
            return

    # Signal 3: Raw HTML
    html_lower = html.lower()
    for pattern in WHATSAPP_PATTERNS:
        if pattern in html_lower:
            idx = html_lower.index(pattern)
            context = html[max(0, idx - 100) : idx + 200]
            number = _extract_number_from_wa_link(context)
            lead.whatsapp_status = WhatsAppStatus.DETECTED
            if number:
                lead.whatsapp_number = number
            lead.lead_score += 10
            lead.lead_score_breakdown["whatsapp_html"] = 10
            logger.info(f"[{lead.name}] WhatsApp in HTML")
            return


# ─────────────────────────────────────────────
# DEEP SOCIAL MEDIA EXTRACTION (5 methods)
# ─────────────────────────────────────────────


async def _deep_extract_social_media(page: Page, html: str, lead: LeadProfile):
    """
    Extract social media URLs using 5 complementary methods:
    1. <a href> scan (all links on page)
    2. Footer-specific scan (targeted)
    3. JSON-LD / Schema.org structured data
    4. OpenGraph meta tags
    5. Icon-based links (Font Awesome, SVG icons)
    """

    socials: Dict[str, Any] = {}

    # ── METHOD 1: All <a href> links on page ──
    links = await page.query_selector_all("a[href]")
    for link in links:
        try:
            href = await link.get_attribute("href") or ""
            href_lower = href.lower()
            if any(x in href_lower for x in ["facebook.com", "fb.com"]):
                socials["facebook_url"] = href
            elif any(x in href_lower for x in ["instagram.com", "instagr.am"]):
                socials["instagram_url"] = href
            elif "tiktok.com" in href_lower:
                socials["tiktok_url"] = href
            elif "yelp.com" in href_lower:
                socials["yelp_url"] = href
            elif any(x in href_lower for x in ["wa.me/", "api.whatsapp.com/send", "whatsapp:"]):
                socials["whatsapp_found_on_web"] = True
                # Try to extract the number from the wa.me link
                wa_match = re.search(r'(?:wa\.me/|phone=)(\d+)', href)
                if wa_match:
                    socials["whatsapp_number"] = f"+{wa_match.group(1)}"
        except Exception:
            continue

    # ── METHOD 2: Footer-specific scan ──
    footer_selectors = [
        "footer",
        "[class*='footer']",
        "[id*='footer']",
        "[role='contentinfo']",
    ]
    for sel in footer_selectors:
        footer = await page.query_selector(sel)
        if footer:
            try:
                footer_html = await footer.inner_html()
                for platform, config in SOCIAL_LINK_PATTERNS.items():
                    if platform not in socials:
                        match = re.search(config["regex"], footer_html, re.IGNORECASE)
                        if match:
                            url = match.group(0)
                            if not any(exc in url.lower() for exc in config["exclude"]):
                                if not url.startswith("http"):
                                    url = "https://" + url
                                socials[f"{platform}_url"] = url
            except Exception:
                continue

    # ── METHOD 3: JSON-LD / Schema.org ──
    try:
        json_ld_elements = await page.query_selector_all(
            "script[type='application/ld+json']"
        )
        for el in json_ld_elements:
            try:
                content = await el.inner_text()
                data = json.loads(content)
                # Handle both single object and array
                if isinstance(data, list):
                    for item in data:
                        _extract_from_jsonld(item, socials)
                elif isinstance(data, dict):
                    _extract_from_jsonld(data, socials)
            except (json.JSONDecodeError, Exception):
                continue
    except Exception:
        pass

    # ── METHOD 4: OpenGraph & other meta tags ──
    try:
        meta_tags = await page.query_selector_all("meta[property], meta[name]")
        for meta in meta_tags:
            content = await meta.get_attribute("content") or ""
            if content:
                _check_social_url(content, socials)
    except Exception:
        pass

    # ── METHOD 5: Icon-based links (Font Awesome, SVG) ──
    icon_selectors = [
        "a:has(i[class*='instagram'])",
        "a:has(i[class*='facebook'])",
        "a:has(i[class*='tiktok'])",
        "a:has(i[class*='youtube'])",
        "a:has(i[class*='yelp'])",
        "a:has(svg[class*='instagram'])",
        "a:has(svg[class*='facebook'])",
        "a:has(img[alt*='instagram' i])",
        "a:has(img[alt*='facebook' i])",
    ]
    for selector in icon_selectors:
        try:
            icon_link = await page.query_selector(selector)
            if icon_link:
                href = await icon_link.get_attribute("href") or ""
                _check_social_url(href, socials)
        except Exception:
            continue

    # Update lead with social findings
    lead.instagram_url = socials.get("instagram_url") or lead.instagram_url
    lead.facebook_url = socials.get("facebook_url") or lead.facebook_url
    lead.tiktok_url = socials.get("tiktok_url") or lead.tiktok_url
    lead.yelp_url = socials.get("yelp_url") or lead.yelp_url
    
    if socials.get("whatsapp_found_on_web"):
        lead.whatsapp_found_on_web = True
        lead.whatsapp_status = WhatsAppStatus.DETECTED
        lead.whatsapp_confidence = "HIGH"
        if socials.get("whatsapp_number"):
            lead.whatsapp_number = socials.get("whatsapp_number")


def _check_social_url(url: str, found: Dict[str, Any]):
    """Check if a URL matches any social media platform and add to found dict."""
    if not url or not isinstance(url, str):
        return
    url_lower = url.lower()
    for platform, config in SOCIAL_LINK_PATTERNS.items():
        key = f"{platform}_url"
        if key in found:
            continue  # Already found this one
        if any(domain in url_lower for domain in config["domains"]):
            if not any(exc in url_lower for exc in config["exclude"]):
                if not url.startswith("http"):
                    url = "https://" + url
                found[key] = url
                return


def _extract_from_jsonld(data: dict, found: Dict[str, Any]):
    """Extract social links from a JSON-LD object (Schema.org)."""
    # sameAs field is the standard way to list social profiles
    same_as = data.get("sameAs") or []
    if isinstance(same_as, str):
        same_as = [same_as]
    for url in same_as:
        _check_social_url(url, found)

    # Some use "url" for social profiles in sub-objects
    for key in ["url", "mainEntityOfPage"]:
        val = data.get(key)
        if isinstance(val, str):
            _check_social_url(val, found)


# ─────────────────────────────────────────────
# EDGE CASE DETECTORS
# ─────────────────────────────────────────────


def _is_parked_domain(text: str, html: str) -> bool:
    return any(sig in text or sig in html for sig in PARKED_DOMAIN_SIGNATURES)


def _is_under_construction(text: str, html: str) -> bool:
    return any(sig in text or sig in html for sig in UNDER_CONSTRUCTION_SIGNATURES)


def _is_cloudflare_blocked(text: str, html: str) -> bool:
    return any(sig in text or sig in html for sig in CLOUDFLARE_SIGNATURES)



# ─────────────────────────────────────────────
# COOKIE CONSENT DISMISSAL

# ─────────────────────────────────────────────


async def _dismiss_cookie_consent(page: Page):
    """Try to dismiss cookie consent banners before scanning content."""
    for selector in COOKIE_DISMISS_SELECTORS:
        try:
            btn = await page.query_selector(selector)
            if btn and await btn.is_visible():
                # force=True and no_wait_after=True prevent Playwright from waiting
                # for navigations that shouldn't happen, avoiding hangs.
                await btn.click(timeout=1500, force=True, no_wait_after=True)
                await asyncio.sleep(0.5)
                logger.debug(f"Dismissed cookie consent via: {selector}")
                return
        except Exception:
            continue


# ─────────────────────────────────────────────
# CONTACT EXTRACTION & SCORING
# ─────────────────────────────────────────────


def _extract_phone_from_text(text: str, lead: LeadProfile):
    matches = US_PHONE_REGEX.findall(text)
    if matches:
        phone = re.sub(r"[^\d+]", "", matches[0])
        if len(phone) >= 10:
            if not phone.startswith("+1") and not phone.startswith("1"):
                phone = "+1" + phone
            elif phone.startswith("1") and not phone.startswith("+"):
                phone = "+" + phone
            lead.phone = phone
            lead.phone_unformatted = phone
            logger.info(f"[{lead.name}] Phone from website: {phone}")


def _score_website_quality(html_lower: str, lead: LeadProfile):
    for sig in LEGACY_SIGNATURES:
        if sig in html_lower:
            lead.lead_score += 5
            lead.lead_score_breakdown[f"legacy_{sig.replace('.', '')}"] = 5
            break

    for sig in MODERN_SIGNATURES:
        if sig in html_lower:
            lead.lead_score -= 5
            lead.lead_score_breakdown["modern_tech"] = -5
            break

    if "viewport" not in html_lower:
        lead.lead_score += 10
        lead.lead_score_breakdown["no_viewport"] = 10


def _extract_social_from_url(url: str, lead: LeadProfile):
    if "instagram.com" in url:
        lead.instagram_url = url
    elif "facebook.com" in url:
        lead.facebook_url = url
    elif "tiktok.com" in url:
        lead.tiktok_url = url


# ─────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────


def _is_social_media_url(url: str) -> bool:
    social_domains = [
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "linkedin.com",
    ]
    return any(domain in url.lower() for domain in social_domains)


def _is_booking_platform_url(url: str) -> Optional[str]:
    url_lower = url.lower()
    for platform, data in BOOKING_PLATFORMS.items():
        if any(sig in url_lower for sig in data["urls"]):
            return platform
    return None


def _extract_number_from_wa_link(text: str) -> Optional[str]:
    match = re.search(r"(?:wa\.me/|phone=|send\?phone=)(\+?\d{10,15})", text)
    if match:
        number = match.group(1)
        if not number.startswith("+"):
            number = "+" + number
        return number
    return None
