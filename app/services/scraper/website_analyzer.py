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
from typing import Optional, List, Dict
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


async def analyze_lead_website(lead: LeadProfile) -> LeadProfile:
    """
    Deep website intelligence gathering using Playwright.
    Enriches a LeadProfile already populated by Apify.
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

    # ── FULL BROWSER ANALYSIS ──
    logger.info(f"[{lead.name}] Starting browser analysis: {url}")

    return await _safe_playwright_call(_browser_analyze_lead, lead, url)


async def _browser_analyze_lead(lead: LeadProfile, url: str) -> LeadProfile:
    """Inner function that does the actual Playwright browser work."""
    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                ignore_https_errors=True,
            )
            page = await context.new_page()

            # ── NAVIGATION WITH FALLBACK ──
            # Try networkidle first (best for JS-heavy sites)
            # Fall back to domcontentloaded (faster, works for slow sites)
            response = None
            try:
                response = await page.goto(url, wait_until="networkidle", timeout=20000)
            except Exception:
                logger.info(
                    f"[{lead.name}] networkidle timeout, trying domcontentloaded..."
                )
                try:
                    response = await page.goto(
                        url, wait_until="domcontentloaded", timeout=15000
                    )
                except Exception as nav_err:
                    error_msg = str(nav_err).lower()

                    # ── EDGE CASE: DNS failure / Connection refused ──
                    if (
                        "net::err_name_not_resolved" in error_msg
                        or "net::err_connection_refused" in error_msg
                    ):
                        lead.category = LeadCategory.NO_WEBSITE
                        lead.website_status = WebsiteStatus.DEAD
                        lead.lead_score += 20
                        lead.lead_score_breakdown["dead_website"] = 20
                        logger.info(
                            f"[{lead.name}] Website is DEAD (DNS/connection failure). Category: NO_WEBSITE"
                        )
                        await browser.close()
                        return lead

                    # ── EDGE CASE: Timeout ──
                    if "timeout" in error_msg:
                        lead.website_status = WebsiteStatus.TIMEOUT
                        lead.lead_score += 10
                        lead.lead_score_breakdown["slow_website"] = 10
                        logger.info(
                            f"[{lead.name}] Website TIMEOUT. Keeping as STATIC_WEBSITE."
                        )
                        await browser.close()
                        return lead

                    # ── Any other navigation error ──
                    lead.website_status = WebsiteStatus.ERROR
                    logger.error(f"[{lead.name}] Navigation error: {nav_err}")
                    await browser.close()
                    return lead

            # ── REDIRECT TO BOOKING PLATFORM ──
            final_url = page.url
            redirect_platform = _is_booking_platform_url(final_url)
            if redirect_platform:
                lead.category = LeadCategory.FULLY_AUTOMATED
                lead.booking_system = redirect_platform
                lead.website_status = WebsiteStatus.LIVE
                logger.info(f"[{lead.name}] Redirected to {redirect_platform}")
                await browser.close()
                return lead

            # ── HTTP ERROR ──
            if response and response.status >= 400:
                lead.category = LeadCategory.NO_WEBSITE
                lead.website_status = WebsiteStatus.ERROR
                lead.lead_score += 10
                lead.lead_score_breakdown["broken_website"] = 10
                logger.warning(f"[{lead.name}] HTTP {response.status}")
                await browser.close()
                return lead

            # ── GET PAGE CONTENT ──
            page_html = await page.content()
            page_text = ""
            try:
                page_text = await page.inner_text("body")
            except Exception:
                pass

            page_html_lower = page_html.lower()
            page_text_lower = page_text.lower()

            # ── EDGE CASE: PARKED DOMAIN ──
            if _is_parked_domain(page_text_lower, page_html_lower):
                lead.category = LeadCategory.NO_WEBSITE
                lead.website_status = WebsiteStatus.PARKED
                lead.lead_score += 20
                lead.lead_score_breakdown["parked_domain"] = 20
                logger.info(
                    f"[{lead.name}] PARKED domain detected. Category: NO_WEBSITE"
                )
                await browser.close()
                return lead

            # ── EDGE CASE: UNDER CONSTRUCTION ──
            if _is_under_construction(page_text_lower, page_html_lower):
                lead.category = LeadCategory.NO_WEBSITE
                lead.website_status = WebsiteStatus.UNDER_CONSTRUCTION
                lead.lead_score += 25  # Highest score — they're actively building!
                lead.lead_score_breakdown["under_construction"] = 25
                logger.info(f"[{lead.name}] UNDER CONSTRUCTION. Category: NO_WEBSITE")
                await browser.close()
                return lead

            # ── EDGE CASE: CLOUDFLARE CHALLENGE ──
            if _is_cloudflare_blocked(page_text_lower, page_html_lower):
                lead.category = LeadCategory.STATIC_WEBSITE
                lead.website_status = WebsiteStatus.CLOUDFLARE_BLOCKED
                lead.lead_score += 5
                lead.lead_score_breakdown["cloudflare_basic"] = 5
                logger.info(
                    f"[{lead.name}] Cloudflare challenge detected. Assuming STATIC."
                )
                await browser.close()
                return lead

            # ── Site is LIVE ──
            lead.website_status = WebsiteStatus.LIVE

            # ── DISMISS COOKIE CONSENT ──
            await _dismiss_cookie_consent(page)

            # ── STEP 1: BOOKING SYSTEM DETECTION ──
            booking = await _detect_booking_system(page, page_html)

            if booking:
                lead.category = LeadCategory.FULLY_AUTOMATED
                lead.booking_system = booking
                logger.info(f"[{lead.name}] Booking system: {booking}")
            else:
                booking_on_subpage = await _check_subpages_for_booking(page, url)
                if booking_on_subpage:
                    lead.category = LeadCategory.FULLY_AUTOMATED
                    lead.booking_system = booking_on_subpage
                    logger.info(
                        f"[{lead.name}] Booking on subpage: {booking_on_subpage}"
                    )
                else:
                    lead.category = LeadCategory.STATIC_WEBSITE
                    lead.lead_score += 15
                    lead.lead_score_breakdown["static_website"] = 15
                    logger.info(f"[{lead.name}] STATIC_WEBSITE (no booking)")

            # ── STEP 2: WHATSAPP FROM WEBSITE ──
            await _detect_whatsapp_from_page(page, page_html, lead)

            # ── STEP 3: PHONE BACKUP ──
            if not lead.phone and not lead.phone_unformatted:
                _extract_phone_from_text(page_text, lead)

            # ── STEP 4: DEEP SOCIAL MEDIA EXTRACTION (5 methods) ──
            await _deep_extract_social_media(page, page_html, lead)

            # ── STEP 5: WEBSITE QUALITY SCORING ──
            _score_website_quality(page_html_lower, lead)

            # ── STEP 6: OSINT SOCIAL RESOLVER (FALLBACK) ──
            if not lead.instagram_url or not lead.facebook_url:
                await resolve_missing_socials(lead)

            # ── CLAMP FINAL SCORE ──
            lead.lead_score = max(0, min(100, lead.lead_score))

            await browser.close()
            return lead

        except Exception as e:
            err_msg = str(e)
            if "execution context was destroyed" in err_msg.lower():
                logger.warning(
                    f"[{lead.name}] Navigation occurred during analysis. Returning partial data."
                )
            else:
                logger.error(f"[{lead.name}] Browser analysis failed: {err_msg}")

            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            return lead


# ─────────────────────────────────────────────
# WHATSAPP PHONE NUMBER VERIFICATION
# ─────────────────────────────────────────────


async def verify_whatsapp_number(lead: LeadProfile) -> LeadProfile:
    """
    Proactively check if a salon's phone number is registered on WhatsApp.
    Uses api.whatsapp.com/send?phone=<number> endpoint.

    DETECTION LOGIC (verified via real browser inspection):
    - Valid number: Page heading shows "Chat on WhatsApp with +1 XXX-XXX-XXXX"
      (formatted with dashes) + "Open app" button + "Continue to WhatsApp Web"
    - Invalid number: Page shows "Phone number shared via url is invalid"
      OR the number appears unformatted (no dashes) in the heading

    This is a SEPARATE step, called AFTER analyze_lead_website.
    Rate-limited: call with asyncio.sleep(2) between salons.
    """
    # Skip if already detected via website
    if lead.whatsapp_status == WhatsAppStatus.DETECTED:
        return lead

    # Need a phone number to check
    phone = lead.phone_unformatted or lead.phone
    if not phone:
        lead.whatsapp_status = WhatsAppStatus.NOT_DETECTED
        return lead

    # Clean phone for wa.me format (digits only, starting with country code)
    clean_phone = re.sub(r"[^\d]", "", phone)

    # If it was originally formatted with a +, trust it completely
    if phone.startswith("+"):
        pass  # Use clean_phone as is
    # Otherwise, try to apply the lead's country code prefix
    elif lead.country_code:
        # Simple mapping for common ones if not already prefixed
        if lead.country_code == "US" and not clean_phone.startswith("1"):
            clean_phone = "1" + clean_phone
        elif lead.country_code == "IN" and not clean_phone.startswith("91"):
            clean_phone = "91" + clean_phone
        # Fallback: if lead.country_code is a digit string, use it
        elif lead.country_code.isdigit():
            if not clean_phone.startswith(lead.country_code):
                clean_phone = lead.country_code + clean_phone

    wa_url = f"https://api.whatsapp.com/send?phone={clean_phone}"

    logger.info(
        f"[{lead.name}] Checking Global WhatsApp: +{clean_phone} (Region: {lead.country_code or 'UNKNOWN'})"
    )

    return await _safe_playwright_call(_browser_verify_whatsapp, lead, clean_phone)


async def _browser_verify_whatsapp(lead: LeadProfile, clean_phone: str) -> LeadProfile:
    """Inner function that does the actual WhatsApp Playwright verification."""
    wa_url = f"https://api.whatsapp.com/send?phone={clean_phone}"

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                ignore_https_errors=True,
            )
            page = await context.new_page()

            await page.goto(wa_url, wait_until="networkidle", timeout=15000)

            # Wait for the page to fully render
            await asyncio.sleep(3)

            # ── LAYER 3: SIGNAL ANALYSIS ──
            page_text = (await page.inner_text("body")).lower()

            # The "Pro" signals from the user's manual verification
            valid_signals = [
                "open app",  # Green CTA button
                "continue to whatsapp web",  # Secondary button
                "download it now",  # Fallback link
            ]

            # ── THE "DASH FORMATTING" TRICK (High Confidence) ──
            # WhatsApp formats valid numbers with dashes (+1 214-923-7138)
            # but shows invalid numbers as raw digits.
            is_valid = any(sig in page_text for sig in valid_signals)
            has_valid_heading = False
            is_raw_digit_fail = False

            if "chat on whatsapp with" in page_text:
                # Extract the part of the text that contains the phone number
                try:
                    # Look for the number after the heading text
                    header_content = page_text.split("chat on whatsapp with")[1].strip()
                    # Grab a chunk of the number (e.g. "+1 214-923-7138")
                    number_part = header_content[:25]

                    # Check if it contains a dash (strongest signal for validity)
                    if "-" in number_part:
                        has_valid_heading = True
                        logger.debug(
                            f"[{lead.name}] Dash formatting detected in WhatsApp heading: {number_part}"
                        )
                    else:
                        # If it's just raw digits, it's likely an invalid number
                        # (WhatsApp doesn't format it if the account doesn't exist)
                        is_raw_digit_fail = True
                        logger.debug(
                            f"[{lead.name}] Raw digit number detected (likely invalid): {number_part}"
                        )
                except Exception:
                    pass

            # ── DECISION LOGIC ──
            if is_raw_digit_fail:
                lead.whatsapp_status = WhatsAppStatus.NOT_DETECTED
                logger.info(
                    f"[{lead.name}] ❌ WhatsApp NOT registered (Raw Digit Check): +{clean_phone}"
                )
            elif has_valid_heading and is_valid:
                lead.whatsapp_status = WhatsAppStatus.DETECTED
                lead.whatsapp_number = f"+{clean_phone}"
                lead.lead_score += 20
                lead.lead_score_breakdown["whatsapp_verified"] = 20
                logger.info(
                    f"[{lead.name}] ✅ WhatsApp VERIFIED (Dash Formatting): +{clean_phone}"
                )
            elif is_valid:
                # Page shows valid buttons but heading didn't match perfectly (fallback)
                lead.whatsapp_status = WhatsAppStatus.DETECTED
                lead.whatsapp_number = f"+{clean_phone}"
                lead.lead_score += 15
                lead.lead_score_breakdown["whatsapp_likely"] = 15
                logger.info(
                    f"[{lead.name}] ✅ WhatsApp LIKELY (Button Match): +{clean_phone}"
                )
            else:
                lead.whatsapp_status = WhatsAppStatus.UNVERIFIED
                logger.info(f"[{lead.name}] ⚠️ WhatsApp inconclusive: +{clean_phone}")

            await browser.close()
            return lead

        except Exception as e:
            err_msg = str(e)
            if "execution context was destroyed" in err_msg.lower():
                logger.warning(
                    f"[{lead.name}] WhatsApp check interrupted by navigation."
                )
            else:
                logger.error(f"[{lead.name}] WhatsApp verification failed: {err_msg}")

            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
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


async def _check_subpages_for_booking(page: Page, base_url: str) -> Optional[str]:
    """Check common subpages for booking systems."""
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    for subpage in BOOKING_SUBPAGES[:3]:
        try:
            sub_url = f"{base}{subpage}"
            response = await page.goto(
                sub_url, wait_until="domcontentloaded", timeout=10000
            )
            if response and response.status < 400:
                redirect_platform = _is_booking_platform_url(page.url)
                if redirect_platform:
                    return redirect_platform
                sub_html = await page.content()
                html_lower = sub_html.lower()
                for platform, data in BOOKING_PLATFORMS.items():
                    if any(sig in html_lower for sig in data["urls"]):
                        return platform
        except Exception:
            continue
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

    found_socials: Dict[str, str] = {}

    # ── METHOD 1: All <a href> links on page ──
    links = await page.query_selector_all("a[href]")
    for link in links:
        try:
            href = await link.get_attribute("href") or ""
            _check_social_url(href, found_socials)
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
                    if platform not in found_socials:
                        match = re.search(config["regex"], footer_html, re.IGNORECASE)
                        if match:
                            url = match.group(0)
                            if not any(exc in url.lower() for exc in config["exclude"]):
                                if not url.startswith("http"):
                                    url = "https://" + url
                                found_socials[platform] = url
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
                        _extract_from_jsonld(item, found_socials)
                elif isinstance(data, dict):
                    _extract_from_jsonld(data, found_socials)
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
                _check_social_url(content, found_socials)
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
                _check_social_url(href, found_socials)
        except Exception:
            continue

    # ── APPLY found socials to salon ──
    if "instagram" in found_socials and not lead.instagram_url:
        lead.instagram_url = found_socials["instagram"]
        lead.lead_score += 5
        lead.lead_score_breakdown["instagram_found"] = 5
        logger.info(f"[{lead.name}] Instagram: {found_socials['instagram']}")

    if "facebook" in found_socials and not lead.facebook_url:
        lead.facebook_url = found_socials["facebook"]
        logger.info(f"[{lead.name}] Facebook: {found_socials['facebook']}")

    if "tiktok" in found_socials and not lead.tiktok_url:
        lead.tiktok_url = found_socials["tiktok"]
        logger.info(f"[{lead.name}] TikTok: {found_socials['tiktok']}")

    if "youtube" in found_socials and not lead.youtube_url:
        lead.youtube_url = found_socials["youtube"]
        logger.info(f"[{lead.name}] YouTube: {found_socials['youtube']}")

    if "yelp" in found_socials and not lead.yelp_url:
        lead.yelp_url = found_socials["yelp"]
        logger.info(f"[{lead.name}] Yelp: {found_socials['yelp']}")


def _check_social_url(url: str, found: Dict[str, str]):
    """Check if a URL matches any social media platform and add to found dict."""
    if not url or not isinstance(url, str):
        return
    url_lower = url.lower()
    for platform, config in SOCIAL_LINK_PATTERNS.items():
        if platform in found:
            continue  # Already found this one
        if any(domain in url_lower for domain in config["domains"]):
            if not any(exc in url_lower for exc in config["exclude"]):
                if not url.startswith("http"):
                    url = "https://" + url
                found[platform] = url
                return


def _extract_from_jsonld(data: dict, found: Dict[str, str]):
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
                await btn.click()
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
