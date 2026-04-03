"""
Deep Pydantic serialization test — reproduces the exact failure scenarios.
Runs with: python test_pydantic.py
"""
import warnings
import sys

# Capture ALL UserWarnings so we can assert they don't fire
warning_log = []
original_showwarning = warnings.showwarning
def capture_warning(message, category, filename, lineno, file=None, line=None):
    warning_log.append(str(message))
    original_showwarning(message, category, filename, lineno, file, line)
warnings.showwarning = capture_warning
warnings.simplefilter("always")

from app.models.lead import (
    LeadProfile, ReviewDistribution, Review,
    LeadCategory, WhatsAppStatus, WebsiteStatus
)

PASS = 0
FAIL = 0

def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}  {detail}")
        FAIL += 1

print("\n── ReviewDistribution ──")
warning_log.clear()

# Case 1: Normal ints
rd = ReviewDistribution(one_star=3, two_star=1, three_star=2, four_star=10, five_star=22)
d = rd.model_dump()
check("Normal ints serialise correctly", d == {"one_star":3,"two_star":1,"three_star":2,"four_star":10,"five_star":22})
check("No Pydantic warning (normal ints)", not any("Expected" in w for w in warning_log))

# Case 2: Apify nested dict format  {"1": {"count": 3}, ...}
warning_log.clear()
rd2 = ReviewDistribution(
    one_star={"count": 3},
    two_star={"count": 1},
    three_star={"count": 2},
    four_star={"count": 10},
    five_star={"count": 22},
)
d2 = rd2.model_dump()
check("Dict values converted to ints", all(isinstance(v, int) for v in d2.values()))
check("No Pydantic warning (dict input)", not any("Expected" in w for w in warning_log))

# Case 3: Strings and None
warning_log.clear()
rd3 = ReviewDistribution(one_star="5", two_star=None, three_star="2.0", four_star=0, five_star="10")
d3 = rd3.model_dump()
check("String/None values converted correctly", d3["two_star"] == 0 and d3["one_star"] == 5)
check("No warning (string/None input)", not any("Expected" in w for w in warning_log))

print("\n── Review ──")
warning_log.clear()

# Case 4: Stars as dict
r = Review(reviewer_name="Alice", text="Great!", stars={"value": 5}, published_at="2024-01-01")
d4 = r.model_dump()
check("stars dict converted to int", d4["stars"] == 5)
check("No warning (stars as dict)", not any("Expected" in w for w in warning_log))

# Case 5: stars as None
r2 = Review(stars=None)
d5 = r2.model_dump()
check("stars None stays None", d5["stars"] is None)

print("\n── LeadProfile numeric fields ──")
warning_log.clear()

lp = LeadProfile(
    name="Test Salon",
    google_review_count={"count": 42},   # dict — Apify bug
    google_rating={"total": 4.3},        # dict — Apify bug
    lead_score=0,
)
# simulate item assignment (bypasses validate_assignment)
lp.lead_score_breakdown["static_website"] = 15
lp.lead_score_breakdown["whatsapp_html"] = 10

d6 = lp.model_dump()
check("google_review_count dict→int", d6["google_review_count"] == 42)
check("google_rating dict→float", d6["google_rating"] == 4.3)
check("lead_score_breakdown int values", all(isinstance(v, int) for v in d6["lead_score_breakdown"].values()))
check("No warning (LeadProfile dict fields)", not any("Expected" in w for w in warning_log))

print("\n── booking_links / order_links (mixed types from Apify) ──")
warning_log.clear()

lp2 = LeadProfile(
    name="Salon B",
    booking_links=[{"url": "https://vagaro.com/salon", "type": "booking", "count": 1}],
    order_links=[{"url": "https://uber.com/eats/salon", "active": True}],
)
d7 = lp2.model_dump()
check("Mixed booking_links serialise without warning", isinstance(d7["booking_links"], list))
check("No warning (booking_links mixed types)", not any("Expected" in w for w in warning_log))

print(f"\n{'='*50}")
print(f"PASS: {PASS}  FAIL: {FAIL}  TOTAL: {PASS+FAIL}")
pydantic_warnings = [w for w in warning_log if "Expected" in w or "serializer" in w.lower()]
if pydantic_warnings:
    print(f"\n⚠️  Pydantic warnings still firing:")
    for w in pydantic_warnings:
        print(f"   {w}")
else:
    print("✅ Zero Pydantic serialization warnings")
sys.exit(0 if FAIL == 0 else 1)
