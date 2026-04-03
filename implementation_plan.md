# Beauty Salon Intelligence System — Complete Rebuild Plan

## Problem Statement

The current system is a general-purpose Indian B2B lead enrichment tool using Tavily + httpx for scraping. The new requirements demand a **fundamentally different** architecture:
- **Google Maps as the primary data source** (structured business data, reviews, ratings)
- **Deep website crawling** with JavaScript rendering (Playwright) to detect booking systems
- **3-Category classification** based on digital presence analysis
- **Instagram activity signals** (last post date, follower activity)
- **Negative review sentiment analysis** (specifically for "phone complaints")
- **Location-driven campaign generation** (input a city → get filtered leads)

> [!IMPORTANT]
> **This is NOT a patch.** The scraper, models, agents, and pipeline all need to be redesigned from the ground up. The existing `AgentExecutor` and `OutreachWriter` can stay — everything upstream gets rebuilt.

## Research Findings: Scraping Tools

| Tool | Strengths | Weaknesses | Cost | Recommendation |
|------|-----------|------------|------|----------------|
| **Apify Google Maps Scraper** | Production-ready, handles anti-bot, returns structured JSON (name, phone, address, website, reviews, rating, hours) | Pay-per-result (~$0.002-0.004/place + add-ons) | ~$5-15 per 1000 leads | ✅ **PRIMARY — use this** |
| **Playwright (Python)** | Full JS rendering, detect iframes/booking widgets, crawl individual websites | You build/maintain it yourself | Free (open source) | ✅ **SECONDARY — for website categorization** |
| **Tavily** | AI-synthesized answers | Cannot do structured GMaps data, can't render JS | Current plan | Keep for fallback enrichment only |
| **SerpAPI** | Google Maps local results | Per-request pricing, less data than Apify | ~$50/mo for 5000 searches | ❌ Skip |
| **gosom/google-maps-scraper** | Open source, Docker-based | Requires self-hosting, proxy management | Free + proxy costs | Backup if Apify budget is tight |

## Proposed Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    API Layer (FastAPI)                   │
│  POST /api/campaign    — Start location-based campaign  │
│  POST /api/upload      — Ingest existing lead database  │
│  GET  /api/results/:id — Get campaign results           │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│              Orchestrator (pipeline.py)                  │
│  For each lead: Discovery → Categorize → Contact → Msg  │
└───────────────────────┬─────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┬─────────────┐
        ▼               ▼               ▼             ▼
┌──────────────┐ ┌─────────────┐ ┌───────────┐ ┌──────────┐
│  Discovery   │ │ Categorizer │ │  Contact   │ │ Outreach │
│    Agent     │ │    Agent    │ │  Finder    │ │  Writer  │
│  (Apify +    │ │ (Playwright │ │ (enhanced) │ │(existing)│
│   Tavily)    │ │  + LLM)     │ │            │ │          │
└──────────────┘ └─────────────┘ └───────────┘ └──────────┘
```

## Proposed Changes

---

### Data Models

#### [MODIFY] [business.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/models/business.py)
Complete rewrite to match beauty salon requirements:
- Add `address`, `city`, `state`, `zip_code` fields
- Add `google_rating`, `google_review_count` fields
- Add `category` enum: `STATIC_WEBSITE`, `NO_WEBSITE`, `FULLY_AUTOMATED`
- Add `instagram_url`, `instagram_last_post`, `facebook_url`
- Add `booking_system_detected` (specific platform name)
- Add `negative_review_signals` (list of phone-complaint reviews)
- Add `lead_quality_score` (composite score 0-100)

#### [NEW] [lead.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/models/lead.py)
New model for the enriched lead output combining all data.

---

### Services Layer (Complete Scraper Rebuild)

#### [NEW] [app/services/gmaps/client.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/services/gmaps/client.py)
Apify Google Maps integration:
- `search_salons(query, location)` → returns structured business data
- `get_reviews(place_id, max_reviews=50)` → returns reviews for sentiment analysis
- Handles rate limiting, cost limits, and error recovery

#### [NEW] [app/services/gmaps/review_analyzer.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/services/gmaps/review_analyzer.py)
LLM-powered review analysis:
- Identify reviews mentioning phone/booking frustrations
- Extract sentiment signals for lead quality scoring

#### [NEW] [app/services/website_crawler/crawler.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/services/website_crawler/crawler.py)
Playwright-based deep website analysis:
- Detect booking system iframes (Calendly, Fresha, Mindbody, Vagaro, Phorest, Acuity, etc.)
- Detect "Book Now" CTAs and booking-related links
- Assess website quality (modern vs. static/outdated)
- Extract social media links from the website
- Return `WebsiteCrawlResult` with category determination

#### [NEW] [app/services/social/instagram.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/services/social/instagram.py)
Instagram activity checker:
- Verify if Instagram profile exists and is active
- Get last post date (via Apify Instagram scraper or public profile page)
- Add `has_instagram`, `last_post_date` columns

---

### Agents Layer

#### [MODIFY] [researcher/agent.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/agents/researcher/agent.py)
Rewrite to:
1. Call Apify Google Maps scraper for the target location + "Indian beauty salon" query
2. Parse structured results (name, phone, address, website, rating, reviews)
3. For each result with a website → trigger `Categorizer Agent`
4. For each result without a website → auto-classify as Category 2

#### [NEW] [categorizer/agent.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/agents/categorizer/agent.py)
New agent that:
1. Uses Playwright to deeply crawl the salon's website
2. Scans for booking system indicators (iframe src, link hrefs, script tags)
3. Assesses website quality (static HTML vs. modern React/framework)
4. Returns one of the 3 categories with confidence

#### [MODIFY] [contact_finder/agent.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/agents/contact_finder/agent.py)
Simplify significantly — Google Maps already provides phone. This agent becomes a validator:
- Verify phone from Maps is real
- Extract email from website crawl results
- Look for social media contact points

#### [MODIFY] [outreach_writer/prompt.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/agents/outreach_writer/prompt.py)
Update prompt to be beauty-salon specific:
- Reference their Google rating/reviews
- Tailor based on their category (no website → offer website, static → offer upgrade, etc.)

---

### Pipeline & API

#### [MODIFY] [pipeline.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/orchestrator/pipeline.py)
New flow: `Discovery → Categorize → Contact Validate → Review Analyze → Outreach`

#### [MODIFY] [routes.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/api/routes.py)
Add new endpoint: `POST /api/campaign` that accepts `{ "location": "Dallas, TX", "niche": "indian beauty salon" }`

#### [MODIFY] [excel_processor.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/services/file_processor/excel_processor.py)
Update to handle existing lead database ingestion (re-categorization pipeline)

---

### Frontend

#### [MODIFY] [index.html](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/frontend/index.html)
- Add "New Campaign" mode (location input instead of just file upload)
- Add category filter tabs (🟡 Static | 🔴 No Website | 🔵 Automated)
- Add Google rating/review columns

#### [MODIFY] [script.js](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/frontend/script.js)
- Render new data fields (address, rating, reviews, category, Instagram)
- Filter functionality for the 3 categories

---

### Configuration & Dependencies

#### [MODIFY] [settings.py](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/app/config/settings.py)
Add: `APIFY_TOKEN`, `APIFY_COST_LIMIT`, `PLAYWRIGHT_HEADLESS`

#### [MODIFY] [requirements.txt](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/requirements.txt)
Add: `apify-client`, `playwright`

#### [MODIFY] [.env.example](file:///C:/Users/amil/OneDrive/Documents/outreach-ai-system/.env.example)
Add: `APIFY_TOKEN=your_apify_token_here`

---

## User Review Required

> [!IMPORTANT]
> **Apify requires a paid account.** The Google Maps scraper costs ~$0.002-0.004 per place. For a campaign of 500 salons across 5 cities, expect ~$2-5 per run. Do you have an Apify account, or should I design a fallback using the free open-source `gosom/google-maps-scraper` (requires Docker)?

> [!IMPORTANT]
> **Playwright needs to be installed.** It downloads Chromium (~200MB). This is required for website categorization (detecting booking iframes). Confirm this is acceptable for your deployment.

> [!WARNING]
> **Instagram scraping is legally grey.** Public profile scraping is possible but Instagram actively blocks automated access. Options: (a) Apify Instagram scraper (~$0.01/profile), (b) Manual column in the output for the team to fill. Which do you prefer?

## Open Questions

1. **Apify vs. Open Source?** Do you have/want an Apify account, or should I use the free Docker-based scraper?
2. **Instagram approach?** Automated (Apify, costs extra) or manual column?
3. **Review analysis depth?** Should we pull 10, 25, or 50 reviews per salon for sentiment analysis?
4. **Hair salon filters:** The "website quality" filter (50% of list) — should this be a separate mode/toggle, or always applied?

## Verification Plan

### Automated Tests
- Test Apify client with a single location query ("Indian beauty salon Dallas TX")
- Test Playwright categorizer against 3 known websites (one static, one with Calendly, one with no site)
- End-to-end: Run a 10-salon campaign and verify all 3 categories appear

### Manual Verification
- Upload existing lead database and verify re-categorization
- Check that review sentiment correctly flags "phone complaint" reviews
