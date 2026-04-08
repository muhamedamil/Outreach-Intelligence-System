# app/services/dedup/csv_dedup.py
#
# CSV-BASED LEAD DEDUPLICATION ENGINE
#
# PRODUCTION DESIGN:
#   Accepts a previously exported leads CSV and builds an O(1) lookup index
#   using two complementary fingerprint strategies:
#
#   Strategy 1 — place_id (Primary, Perfect):
#     Google's globally unique, immutable identifier for every business.
#     Never changes even if the business renames or moves.
#     This is always preferred when present.
#
#   Strategy 2 — name_city_slug (Fallback, Fuzzy):
#     Normalizes the business name + city to a slug (lowercase, no special chars).
#     Catches duplicates where Apify returns the same business but place_id is
#     missing or differs slightly between two runs.
#
# USAGE:
#   index = build_seen_leads_index(csv_bytes)
#   is_dup = is_duplicate(lead, index)
#
# FUTURE UPGRADE PATH:
#   When you move to Supabase/PostgreSQL, this module becomes a thin adapter.
#   Replace build_seen_leads_index() to query the DB and is_duplicate() stays
#   as-is — the rest of the pipeline requires zero changes.

import io
import re
import logging
import pandas as pd
from typing import Dict, Set, Optional
from app.models.lead import LeadProfile

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# FINGERPRINT ENGINE
# ─────────────────────────────────────────────

def _normalise_slug(text: str) -> str:
    """
    Collapse a string to its most stable, comparison-safe form.
    'Sharma Solar Energy Pvt. Ltd.' → 'sharmasolarenergy'
    """
    text = text.lower()
    # Remove common legal suffixes that vary between data sources
    text = re.sub(r'\b(pvt|ltd|llp|inc|co|the|and|of)\b', '', text)
    # Remove all non-alphanumeric characters
    text = re.sub(r'[^a-z0-9]', '', text)
    return text.strip()


def _make_name_city_slug(name: str, city: str) -> str:
    """Builds a composite fingerprint from business name + city."""
    return f"{_normalise_slug(name)}_{_normalise_slug(city)}"


# ─────────────────────────────────────────────
# INDEX BUILDER
# ─────────────────────────────────────────────

class SeenLeadsIndex:
    """
    An O(1) lookup structure that holds two fingerprint sets:
      - place_ids: exact Google Maps IDs
      - name_city_slugs: fuzzy name+city composite keys
    """
    def __init__(self):
        self.place_ids: Set[str] = set()
        self.name_city_slugs: Set[str] = set()
        self.total_seen: int = 0

    def add(self, place_id: Optional[str], name: str, city: str):
        if place_id and place_id.strip():
            self.place_ids.add(place_id.strip())
        slug = _make_name_city_slug(name, city or "")
        if slug:
            self.name_city_slugs.add(slug)
        self.total_seen += 1


def build_seen_leads_index(csv_bytes: bytes, file_name: str = "seen.csv") -> SeenLeadsIndex:
    """
    Parses a previously exported leads CSV and builds a SeenLeadsIndex.

    Detects the CSV origin automatically:
      - If the CSV has a 'place_id' column, it was exported by this system → use both strategies.
      - If it only has 'name'/'business_name', fall back to name+city slug only.

    Returns an empty index (no dedup) if the file can't be parsed — never crashes.
    """
    index = SeenLeadsIndex()

    try:
        if file_name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(csv_bytes))
        else:
            df = pd.read_csv(io.BytesIO(csv_bytes))

        # Normalise column names to lowercase for robust matching
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        # Detect the name column (exported CSVs use 'name', customer lists may use 'business_name')
        name_col = next((c for c in df.columns if c in ("name", "business_name", "company_name")), None)
        city_col = next((c for c in df.columns if c in ("city", "location", "area")), None)
        place_id_col = "place_id" if "place_id" in df.columns else None

        if not name_col:
            logger.warning(f"[Dedup] Could not find a name column in {file_name}. Skipping dedup.")
            return index

        for _, row in df.iterrows():
            name = str(row.get(name_col, "") or "").strip()
            city = str(row.get(city_col, "") or "").strip() if city_col else ""
            place_id = str(row.get(place_id_col, "") or "").strip() if place_id_col else None

            if not name:
                continue

            index.add(
                place_id=place_id if (place_id and place_id != "nan") else None,
                name=name,
                city=city
            )

        logger.info(
            f"[Dedup] Index built from '{file_name}': "
            f"{len(index.place_ids)} place_ids, "
            f"{len(index.name_city_slugs)} name+city slugs "
            f"({index.total_seen} total seen leads)"
        )

    except Exception as e:
        logger.error(f"[Dedup] Failed to parse seen-leads CSV: {e}. Proceeding without dedup.")

    return index


# ─────────────────────────────────────────────
# DUPLICATE CHECKER
# ─────────────────────────────────────────────

def is_duplicate(lead: LeadProfile, index: SeenLeadsIndex) -> bool:
    """
    Returns True if this lead already exists in the seen-leads index.

    Check order:
      1. place_id match (exact) — preferred, fastest, most reliable
      2. name+city slug match (fuzzy) — catches same business with missing place_id
    """
    if not index.place_ids and not index.name_city_slugs:
        return False  # Empty index = no dedup

    # Strategy 1: place_id exact match
    if lead.place_id and lead.place_id.strip():
        if lead.place_id.strip() in index.place_ids:
            return True

    # Strategy 2: name + city slug fuzzy match
    city = lead.city or ""
    slug = _make_name_city_slug(lead.name, city)
    if slug and slug in index.name_city_slugs:
        return True

    return False


def filter_new_leads(leads: list, index: SeenLeadsIndex) -> tuple:
    """
    Splits a list of LeadProfile objects into (new_leads, skipped_count).
    Logs a clear summary of what was filtered.
    """
    if not index.total_seen:
        return leads, 0  # No index — pass everything through

    new_leads = []
    skipped = []

    for lead in leads:
        if is_duplicate(lead, index):
            skipped.append(lead.name)
        else:
            new_leads.append(lead)

    if skipped:
        logger.info(
            f"[Dedup] Filtered {len(skipped)} already-seen leads: "
            f"{', '.join(skipped[:5])}{'...' if len(skipped) > 5 else ''}"
        )
    logger.info(f"[Dedup] Result: {len(new_leads)} new leads to process, {len(skipped)} skipped.")

    return new_leads, len(skipped)
