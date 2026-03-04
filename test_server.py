#!/usr/bin/env python3
"""
Smoke-Test für den ReMarket MCP Server v0.2.0.
Testet alle 10 Tools direkt ohne MCP-Transport.
"""

import asyncio
import sys
import os

# DB-Config für lokalen Test (Port 5433 = socat forward zum Docker-Container)
os.environ.setdefault("REMARKET_DB_HOST", "localhost")
os.environ.setdefault("REMARKET_DB_PORT", "5433")

from server import (
    SearchInput, GetListingInput, PriceCheckInput, GetSellerInput,
    AnalyzePhotoInput, GenerateListingInput, AutoReplyInput, OptimizeListingInput,
    remarket_search_listings, remarket_get_listing, remarket_check_price,
    remarket_get_categories, remarket_get_seller, remarket_trending,
    remarket_analyze_photo, remarket_generate_listing,
    remarket_auto_reply, remarket_optimize_listing,
    SortBy, Condition, ResponseFormat,
)


async def test_all():
    passed = 0
    failed = 0

    tests = [
        # ── Original 6 Tools ──
        ("search_listings (default)", remarket_search_listings, SearchInput()),
        ("search_listings (Elektronik, max 10€)", remarket_search_listings,
         SearchInput(category="Elektronik", max_price_eur=10.0, limit=5)),
        ("search_listings (JSON)", remarket_search_listings,
         SearchInput(limit=3, response_format=ResponseFormat.JSON)),
        ("search_listings (european only)", remarket_search_listings,
         SearchInput(european_only=True, limit=5)),
        ("get_listing (#1)", remarket_get_listing, GetListingInput(listing_id=1)),
        ("get_listing (#999 - not found)", remarket_get_listing, GetListingInput(listing_id=999)),
        ("check_price (Sport)", remarket_check_price,
         PriceCheckInput(category="Sport")),
        ("check_price (Elektronik, dein Preis 25€)", remarket_check_price,
         PriceCheckInput(category="Elektronik", your_price_eur=25.0)),
        ("get_categories", remarket_get_categories, None),
        ("get_seller (id=1)", remarket_get_seller, GetSellerInput(seller_id=1)),
        ("get_seller (username=expo_ella)", remarket_get_seller,
         GetSellerInput(username="expo_ella")),
        ("trending", remarket_trending, None),

        # ── SellBuddy 4 neue Tools ──
        ("analyze_photo (Rucksack)", remarket_analyze_photo,
         AnalyzePhotoInput(
             photo_description="Roter Fjällräven Kånken Rucksack, leichte Gebrauchsspuren "
             "an den Trägern, Reißverschluss funktioniert, keine Löcher.",
             photo_quality="high"
         )),
        ("analyze_photo (IKEA Regal)", remarket_analyze_photo,
         AnalyzePhotoInput(
             photo_description="Weißer IKEA KALLAX Regal 4x2, eine kleine Delle an der Seite, "
             "alle Fächer intakt.",
             photo_quality="medium"
         )),
        ("analyze_photo (low quality)", remarket_analyze_photo,
         AnalyzePhotoInput(
             photo_description="Irgendein Ding, dunkel, unscharf",
             photo_quality="low"
         )),
        ("generate_listing (Rucksack)", remarket_generate_listing,
         GenerateListingInput(
             category="Kleidung", subcategory="Rucksack",
             brand="Fjällräven", condition="gut",
             defects=["Gebrauchsspuren"], city="Hannover-Linden-Nord",
             seller_id=1, description_extra="Ca. 16L, passt perfekt als Uni-Rucksack."
         )),
        # auto_reply – Listing-ID wird dynamisch (nutze #1 als bestehend)
        ("auto_reply (Preisverhandlung)", remarket_auto_reply,
         AutoReplyInput(
             listing_id=1, buyer_message="Ist der Preis verhandelbar? Was letzte Preis?",
             seller_style="freundlich", min_price_eur=10.0
         )),
        ("auto_reply (Verfügbarkeit)", remarket_auto_reply,
         AutoReplyInput(
             listing_id=1, buyer_message="Ist das Teil noch verfügbar?",
             seller_style="locker"
         )),
        ("auto_reply (Versand)", remarket_auto_reply,
         AutoReplyInput(
             listing_id=1, buyer_message="Können Sie das per DHL verschicken?",
             seller_style="sachlich"
         )),
        ("auto_reply (Zustand)", remarket_auto_reply,
         AutoReplyInput(
             listing_id=1, buyer_message="Hat das Teil Kratzer oder Defekte?",
             seller_style="freundlich"
         )),
        ("optimize_listing (#1, nur Vorschläge)", remarket_optimize_listing,
         OptimizeListingInput(listing_id=1, apply_changes=False)),
    ]

    for name, fn, params in tests:
        try:
            if params is not None:
                result = await fn(params)
            else:
                result = await fn()

            if result and len(result) > 10:
                print(f"  ✅ {name} – {len(result)} chars")
                passed += 1
            else:
                print(f"  ⚠️  {name} – kurze Antwort: {result[:80]}")
                passed += 1
        except Exception as e:
            print(f"  ❌ {name} – {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Ergebnis: {passed} passed, {failed} failed von {len(tests)} Tests")
    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(test_all())
    sys.exit(0 if ok else 1)
