#!/usr/bin/env python3
"""
ReMarket MCP Server – Agent-Commerce für Secondhand in Hannover.

Verbindet KI-Agenten mit dem ReMarket-Marktplatz via Model Context Protocol.
10 Tools: search, get_listing, check_price, categories, seller, trending,
          + SellBuddy: analyze_photo, generate_listing, auto_reply, optimize_listing.

Transport: stdio (für lokale Integration mit Claude Desktop, Cursor, etc.)
Datenbank: PostgreSQL (marvin_n8n, Docker Container marvin-postgres)
"""

import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

import psycopg2
import psycopg2.extras
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator

# ── Server Setup ──────────────────────────────────────────────────────────

mcp = FastMCP(
    "remarket-mcp-server",
    instructions=(
        "ReMarket – Deutschlands erste Agent-Commerce-Plattform für Secondhand. "
        "Durchsuche 80+ verifizierte Listings in Hannover, vergleiche Preise, "
        "finde Verkäufer mit hohem Trust-Score. SellBuddy: Foto-zu-Listing, "
        "Auto-Reply, Listing-Optimierung. Version 0.2.0."
    ),
)

# ── Database ──────────────────────────────────────────────────────────────

DB_CONFIG = {
    "host": os.environ.get("REMARKET_DB_HOST", "localhost"),
    "port": int(os.environ.get("REMARKET_DB_PORT", "5432")),
    "dbname": os.environ.get("REMARKET_DB_NAME", "marvin_n8n"),
    "user": os.environ.get("REMARKET_DB_USER", "marvin"),
    "password": os.environ.get("REMARKET_DB_PASSWORD", "heart-of-gold-42"),
}


def get_db():
    """Erstellt eine neue DB-Verbindung. Kurzlebig – pro Request."""
    return psycopg2.connect(**DB_CONFIG)


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Führt ein SELECT aus und gibt Liste von Dicts zurück."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


def query_one(sql: str, params: tuple = ()) -> dict | None:
    """Führt ein SELECT aus und gibt ein einzelnes Dict zurück."""
    rows = query(sql, params)
    return rows[0] if rows else None


# ── JSON Encoder für Decimal, datetime etc. ───────────────────────────────

class ReMarketEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def to_json(data) -> str:
    return json.dumps(data, cls=ReMarketEncoder, ensure_ascii=False, indent=2)


# ── Enums & Models ────────────────────────────────────────────────────────

class SortBy(str, Enum):
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    NEWEST = "newest"
    QUALITY = "quality"


class Condition(str, Enum):
    NEU = "neu"
    WIE_NEU = "wie_neu"
    GUT = "gut"
    AKZEPTABEL = "akzeptabel"


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


# ── Tool 1: Search Listings ──────────────────────────────────────────────

class SearchInput(BaseModel):
    query: Optional[str] = Field(
        default=None,
        description="Freitext-Suche in Titel und Beschreibung (z.B. 'Fjällräven', 'Mountainbike')"
    )
    category: Optional[str] = Field(
        default=None,
        description="Kategorie: Elektronik, Kleidung, Möbel, Sport, Haushalt, Bücher, Kinder"
    )
    condition: Optional[Condition] = Field(
        default=None,
        description="Zustand: neu, wie_neu, gut, akzeptabel"
    )
    min_price_eur: Optional[float] = Field(
        default=None, ge=0,
        description="Mindestpreis in EUR (z.B. 5.00)"
    )
    max_price_eur: Optional[float] = Field(
        default=None, ge=0,
        description="Höchstpreis in EUR (z.B. 50.00)"
    )
    city: Optional[str] = Field(
        default=None,
        description="Stadtteil in Hannover (z.B. 'Hannover-Linden-Nord', 'Hannover-Mitte')"
    )
    european_only: Optional[bool] = Field(
        default=None,
        description="Nur europäische Produkte anzeigen"
    )
    sustainable_only: Optional[bool] = Field(
        default=None,
        description="Nur nachhaltige Produkte anzeigen"
    )
    sort_by: SortBy = Field(
        default=SortBy.NEWEST,
        description="Sortierung: price_asc, price_desc, newest, quality"
    )
    limit: int = Field(default=20, ge=1, le=50, description="Max. Ergebnisse (1-50)")
    offset: int = Field(default=0, ge=0, description="Offset für Pagination")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Ausgabeformat: markdown oder json"
    )


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def remarket_search_listings(params: SearchInput) -> str:
    """Durchsuche den ReMarket-Marktplatz nach Secondhand-Artikeln.

    Filtere nach Kategorie, Zustand, Preis, Stadtteil und mehr.
    Unterstützt Freitext-Suche in Titel und Beschreibung.
    Alle Listings sind in Hannover und Umgebung.

    Args:
        params: Suchfilter (alle optional – ohne Filter werden die neuesten Listings zurückgegeben)

    Returns:
        Liste von Listings mit Titel, Preis, Zustand, Stadtteil und Qualitätsscore.
    """
    conditions = ["l.status = 'active'"]
    values: list = []

    if params.query:
        conditions.append("(l.title ILIKE %s OR l.description ILIKE %s)")
        values.extend([f"%{params.query}%", f"%{params.query}%"])

    if params.category:
        conditions.append("l.category = %s")
        values.append(params.category)

    if params.condition:
        conditions.append("l.condition = %s")
        values.append(params.condition.value)

    if params.min_price_eur is not None:
        conditions.append("l.price_cents >= %s")
        values.append(int(params.min_price_eur * 100))

    if params.max_price_eur is not None:
        conditions.append("l.price_cents <= %s")
        values.append(int(params.max_price_eur * 100))

    if params.city:
        conditions.append("l.city ILIKE %s")
        values.append(f"%{params.city}%")

    if params.european_only:
        conditions.append("l.is_european_product = true")

    if params.sustainable_only:
        conditions.append("l.is_sustainable = true")

    sort_map = {
        SortBy.PRICE_ASC: "l.price_cents ASC",
        SortBy.PRICE_DESC: "l.price_cents DESC",
        SortBy.NEWEST: "l.created_at DESC",
        SortBy.QUALITY: "l.listing_quality_score DESC",
    }
    order = sort_map[params.sort_by]
    where = " AND ".join(conditions)

    # Count total
    count_sql = f"SELECT count(*) as total FROM remarket_listings l WHERE {where}"
    total = query_one(count_sql, tuple(values))["total"]

    # Fetch page
    sql = f"""
        SELECT l.id, l.title, l.category, l.subcategory, l.price_cents,
               l.condition, l.city, l.listing_quality_score, l.is_european_product,
               l.is_sustainable, l.images_count, l.created_at,
               s.username as seller_name, s.trust_score as seller_trust
        FROM remarket_listings l
        JOIN remarket_sellers s ON l.seller_id = s.id
        WHERE {where}
        ORDER BY {order}
        LIMIT %s OFFSET %s
    """
    values.extend([params.limit, params.offset])
    rows = query(sql, tuple(values))

    # Format prices
    for r in rows:
        r["price_eur"] = r.pop("price_cents") / 100

    result = {
        "total": total,
        "count": len(rows),
        "offset": params.offset,
        "has_more": total > params.offset + len(rows),
        "next_offset": params.offset + len(rows) if total > params.offset + len(rows) else None,
        "listings": rows,
    }

    if params.response_format == ResponseFormat.JSON:
        return to_json(result)

    # Markdown
    lines = [f"## ReMarket Suchergebnisse ({result['count']} von {total})\n"]
    if not rows:
        lines.append("Keine Listings gefunden. Versuche andere Suchkriterien.")
        return "\n".join(lines)

    for r in rows:
        eu = " 🇪🇺" if r.get("is_european_product") else ""
        sus = " ♻️" if r.get("is_sustainable") else ""
        lines.append(
            f"### #{r['id']} {r['title']}{eu}{sus}\n"
            f"- **Preis:** {r['price_eur']:.2f} €\n"
            f"- **Zustand:** {r['condition']} | **Qualität:** {r['listing_quality_score']}/10\n"
            f"- **Ort:** {r['city']}\n"
            f"- **Verkäufer:** {r['seller_name']} (Trust: {r['seller_trust']})\n"
            f"- **Bilder:** {r['images_count']} | **Eingestellt:** {r['created_at'].strftime('%d.%m.%Y')}\n"
        )

    if result["has_more"]:
        lines.append(f"\n*Weitere Ergebnisse: offset={result['next_offset']}*")

    return "\n".join(lines)


# ── Tool 2: Get Single Listing ───────────────────────────────────────────

class GetListingInput(BaseModel):
    listing_id: int = Field(..., description="ID des Listings (z.B. 1, 42)")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def remarket_get_listing(params: GetListingInput) -> str:
    """Hole detaillierte Informationen zu einem einzelnen Listing.

    Gibt alle Details zurück: Titel, Beschreibung, Preis, Zustand,
    Verkäufer-Profil mit Trust-Score und Antwortzeit.

    Args:
        params: Listing-ID und gewünschtes Format

    Returns:
        Vollständige Listing-Details inkl. Verkäufer-Info.
    """
    row = query_one("""
        SELECT l.*, s.username, s.trust_score, s.total_sales,
               s.total_purchases, s.avg_response_time_min, s.friendly_score,
               s.city as seller_city
        FROM remarket_listings l
        JOIN remarket_sellers s ON l.seller_id = s.id
        WHERE l.id = %s
    """, (params.listing_id,))

    if not row:
        return f"Listing #{params.listing_id} nicht gefunden."

    row["price_eur"] = row.pop("price_cents") / 100

    if params.response_format == ResponseFormat.JSON:
        return to_json(row)

    eu = " 🇪🇺 Europäisches Produkt" if row.get("is_european_product") else ""
    sus = " ♻️ Nachhaltig" if row.get("is_sustainable") else ""

    return (
        f"## {row['title']} (#{row['id']})\n\n"
        f"{row['description']}\n\n"
        f"- **Preis:** {row['price_eur']:.2f} €\n"
        f"- **Kategorie:** {row['category']} > {row['subcategory']}\n"
        f"- **Zustand:** {row['condition']}\n"
        f"- **Qualitätsscore:** {row['listing_quality_score']}/10\n"
        f"- **Ort:** {row['city']}, {row['region']}\n"
        f"- **Bilder:** {row['images_count']}\n"
        f"- **Status:** {row['status']}{eu}{sus}\n\n"
        f"### Verkäufer: {row['username']}\n"
        f"- **Trust-Score:** {row['trust_score']}/10\n"
        f"- **Verkäufe:** {row['total_sales']} | **Käufe:** {row['total_purchases']}\n"
        f"- **Ø Antwortzeit:** {row['avg_response_time_min']} min\n"
        f"- **Freundlichkeit:** {row['friendly_score']}/10\n"
        f"- **Standort:** {row['seller_city']}\n"
    )


# ── Tool 3: Price Check ──────────────────────────────────────────────────

class PriceCheckInput(BaseModel):
    category: str = Field(..., description="Kategorie (z.B. 'Elektronik', 'Sport')")
    subcategory: Optional[str] = Field(
        default=None,
        description="Unterkategorie (z.B. 'Smartphone', 'Yogamatte')"
    )
    condition: Optional[Condition] = Field(
        default=None,
        description="Zustand für die Preisbewertung"
    )
    your_price_eur: Optional[float] = Field(
        default=None, ge=0,
        description="Dein Preis in EUR – wird mit dem Markt verglichen"
    )


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def remarket_check_price(params: PriceCheckInput) -> str:
    """Vergleiche einen Preis mit dem aktuellen Markt auf ReMarket.

    Gibt Durchschnittspreis, Spanne (min/max), Median und Anzahl der
    Vergleichs-Listings zurück. Optional: Bewertung ob ein Preis fair ist.

    Args:
        params: Kategorie, optional Unterkategorie/Zustand und Vergleichspreis

    Returns:
        Marktpreis-Analyse mit Einschätzung.
    """
    conditions = ["status = 'active'", "category = %s"]
    values: list = [params.category]

    if params.subcategory:
        conditions.append("subcategory ILIKE %s")
        values.append(f"%{params.subcategory}%")

    if params.condition:
        conditions.append("condition = %s")
        values.append(params.condition.value)

    where = " AND ".join(conditions)

    stats = query_one(f"""
        SELECT count(*) as count,
               avg(price_cents) as avg_price,
               min(price_cents) as min_price,
               max(price_cents) as max_price,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY price_cents) as median_price
        FROM remarket_listings
        WHERE {where}
    """, tuple(values))

    if not stats or stats["count"] == 0:
        return f"Keine Listings in Kategorie '{params.category}' gefunden."

    avg_eur = float(stats["avg_price"]) / 100
    min_eur = float(stats["min_price"]) / 100
    max_eur = float(stats["max_price"]) / 100
    med_eur = float(stats["median_price"]) / 100

    lines = [
        f"## Preischeck: {params.category}",
        f"{'> ' + params.subcategory if params.subcategory else ''}",
        f"{'Zustand: ' + params.condition.value if params.condition else 'Alle Zustände'}\n",
        f"- **Durchschnitt:** {avg_eur:.2f} €",
        f"- **Median:** {med_eur:.2f} €",
        f"- **Spanne:** {min_eur:.2f} € – {max_eur:.2f} €",
        f"- **Vergleichs-Listings:** {stats['count']}",
    ]

    if params.your_price_eur is not None:
        diff_pct = ((params.your_price_eur - avg_eur) / avg_eur) * 100
        if diff_pct < -15:
            verdict = "🟢 Schnäppchen! Deutlich unter Marktpreis."
        elif diff_pct < 5:
            verdict = "🟢 Fairer Preis – im Marktbereich."
        elif diff_pct < 20:
            verdict = "🟡 Leicht über Marktpreis."
        else:
            verdict = "🔴 Deutlich über Marktpreis."

        lines.extend([
            f"\n### Dein Preis: {params.your_price_eur:.2f} €",
            f"- **Abweichung:** {diff_pct:+.1f}% vom Durchschnitt",
            f"- **Einschätzung:** {verdict}",
        ])

    return "\n".join(lines)


# ── Tool 4: Get Categories ───────────────────────────────────────────────

@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def remarket_get_categories() -> str:
    """Liste alle verfügbaren Kategorien auf ReMarket mit Anzahl der Listings.

    Gibt Kategorien mit Unterkategorien, Listing-Counts und
    durchschnittlichen Preisen zurück.

    Returns:
        Übersicht aller Kategorien auf dem Marktplatz.
    """
    rows = query("""
        SELECT category,
               count(*) as count,
               avg(price_cents) as avg_price,
               count(DISTINCT subcategory) as subcategories
        FROM remarket_listings
        WHERE status = 'active'
        GROUP BY category
        ORDER BY count DESC
    """)

    lines = ["## ReMarket Kategorien\n"]
    for r in rows:
        avg_eur = r["avg_price"] / 100
        lines.append(
            f"- **{r['category']}** – {r['count']} Listings "
            f"(Ø {avg_eur:.2f} €, {r['subcategories']} Unterkategorien)"
        )

    # Subcategories per category
    subs = query("""
        SELECT category, subcategory, count(*) as count
        FROM remarket_listings
        WHERE status = 'active'
        GROUP BY category, subcategory
        ORDER BY category, count DESC
    """)

    sub_map: dict[str, list] = {}
    for s in subs:
        sub_map.setdefault(s["category"], []).append(f"{s['subcategory']} ({s['count']})")

    lines.append("\n### Unterkategorien\n")
    for cat, items in sub_map.items():
        lines.append(f"**{cat}:** {', '.join(items)}")

    return "\n".join(lines)


# ── Tool 5: Get Seller Profile ───────────────────────────────────────────

class GetSellerInput(BaseModel):
    seller_id: Optional[int] = Field(default=None, description="Seller-ID")
    username: Optional[str] = Field(default=None, description="Username des Verkäufers")

    @field_validator("username")
    @classmethod
    def at_least_one(cls, v: Optional[str], info) -> Optional[str]:
        # Pydantic v2: info.data enthält bereits validierte Felder
        if v is None and info.data.get("seller_id") is None:
            raise ValueError("Entweder seller_id oder username muss angegeben werden.")
        return v


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def remarket_get_seller(params: GetSellerInput) -> str:
    """Hole das Profil eines Verkäufers auf ReMarket.

    Zeigt Trust-Score, Verkaufshistorie, Antwortzeit, Freundlichkeit
    und aktive Listings.

    Args:
        params: seller_id ODER username

    Returns:
        Verkäufer-Profil mit Statistiken und aktiven Listings.
    """
    if params.seller_id:
        seller = query_one("SELECT * FROM remarket_sellers WHERE id = %s", (params.seller_id,))
    else:
        seller = query_one("SELECT * FROM remarket_sellers WHERE username ILIKE %s", (params.username,))

    if not seller:
        return "Verkäufer nicht gefunden."

    listings = query("""
        SELECT id, title, price_cents, condition, category
        FROM remarket_listings
        WHERE seller_id = %s AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 10
    """, (seller["id"],))

    lines = [
        f"## Verkäufer: {seller['username']}\n",
        f"- **Trust-Score:** {seller['trust_score']}/10",
        f"- **Freundlichkeit:** {seller['friendly_score']}/10",
        f"- **Verkäufe:** {seller['total_sales']} | **Käufe:** {seller['total_purchases']}",
        f"- **Ø Antwortzeit:** {seller['avg_response_time_min']} min",
        f"- **Standort:** {seller['city']}",
        f"- **Dabei seit:** {seller['created_at'].strftime('%d.%m.%Y')}\n",
        f"### Aktive Listings ({len(listings)})\n",
    ]

    for li in listings:
        price = li["price_cents"] / 100
        lines.append(f"- #{li['id']} **{li['title']}** – {price:.2f} € ({li['condition']}, {li['category']})")

    return "\n".join(lines)


# ── Tool 6: Trending ─────────────────────────────────────────────────────

@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def remarket_trending() -> str:
    """Zeige Trends auf dem ReMarket-Marktplatz.

    Gibt die neuesten Listings, beliebteste Kategorien,
    Top-Verkäufer und Durchschnittspreise zurück.

    Returns:
        Trend-Übersicht mit Neuheiten, Top-Kategorien und Top-Sellern.
    """
    # Neueste Listings
    newest = query("""
        SELECT l.id, l.title, l.price_cents, l.category, l.condition,
               l.listing_quality_score, s.username
        FROM remarket_listings l
        JOIN remarket_sellers s ON l.seller_id = s.id
        WHERE l.status = 'active'
        ORDER BY l.created_at DESC
        LIMIT 5
    """)

    # Top-Qualität
    top_quality = query("""
        SELECT l.id, l.title, l.price_cents, l.listing_quality_score, l.category
        FROM remarket_listings l
        WHERE l.status = 'active'
        ORDER BY l.listing_quality_score DESC
        LIMIT 5
    """)

    # Top-Seller
    top_sellers = query("""
        SELECT s.username, s.trust_score, s.friendly_score, s.total_sales,
               count(l.id) as active_listings
        FROM remarket_sellers s
        JOIN remarket_listings l ON l.seller_id = s.id AND l.status = 'active'
        GROUP BY s.id, s.username, s.trust_score, s.friendly_score, s.total_sales
        ORDER BY s.trust_score DESC
        LIMIT 5
    """)

    # Gesamt-Stats
    stats = query_one("""
        SELECT count(*) as total,
               avg(price_cents) as avg_price,
               count(DISTINCT seller_id) as sellers,
               count(DISTINCT category) as categories
        FROM remarket_listings WHERE status = 'active'
    """)

    lines = [
        "## ReMarket Trends\n",
        f"**Marktplatz:** {stats['total']} aktive Listings | "
        f"{stats['sellers']} Verkäufer | {stats['categories']} Kategorien | "
        f"Ø {stats['avg_price'] / 100:.2f} €\n",
        "### Neueste Listings\n",
    ]

    for r in newest:
        price = r["price_cents"] / 100
        lines.append(f"- #{r['id']} **{r['title']}** – {price:.2f} € ({r['category']}, von {r['username']})")

    lines.append("\n### Top Qualität\n")
    for r in top_quality:
        price = r["price_cents"] / 100
        lines.append(f"- #{r['id']} **{r['title']}** – {price:.2f} € (Score: {r['listing_quality_score']}/10)")

    lines.append("\n### Top Verkäufer\n")
    for r in top_sellers:
        lines.append(
            f"- **{r['username']}** – Trust {r['trust_score']}/10, "
            f"{r['total_sales']} Verkäufe, {r['active_listings']} aktive Listings"
        )

    return "\n".join(lines)


# ── Helper: DB Write ──────────────────────────────────────────────────────

def execute(sql: str, params: tuple = ()) -> int:
    """Führt INSERT/UPDATE aus und gibt affected rows zurück."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount
    finally:
        conn.close()


def insert_returning(sql: str, params: tuple = ()) -> dict | None:
    """Führt INSERT ... RETURNING aus und gibt die neue Row zurück."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            conn.commit()
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


# ── Tool 7: SellBuddy – Analyze Photo ───────────────────────────────────

class AnalyzePhotoInput(BaseModel):
    photo_description: str = Field(
        ...,
        description=(
            "Beschreibung des Produktfotos. In v1 beschreibst du was du auf dem "
            "Foto siehst (Produkt, Marke, Zustand, Defekte). In v2 wird dies "
            "durch echte Bildanalyse ersetzt. Beispiel: 'Rotes Fjällräven Kånken "
            "Rucksack, leichte Gebrauchsspuren an den Trägern, keine Löcher, "
            "Reißverschluss funktioniert.'"
        ),
    )
    photo_quality: Optional[str] = Field(
        default=None,
        description="Qualität des Fotos: low, medium, high. Leer = automatisch.",
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "photo_description": "Weißer IKEA KALLAX Regal 2x4, leichte Kratzer an der Seite, "
                    "alle Fächer intakt, kein Wasserschaden.",
                    "photo_quality": "medium",
                }
            ]
        }


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def remarket_analyze_photo(params: AnalyzePhotoInput) -> str:
    """Analysiere ein Produktfoto und erkenne Kategorie, Marke, Zustand und Defekte.

    In v1 arbeitet SellBuddy mit Textbeschreibungen des Fotos.
    Ab v2 wird echte Vision-Analyse (Ollama qwen3.5 multimodal) integriert.

    Die Analyse liefert strukturierte Daten, die direkt in
    remarket_generate_listing weiterverwendet werden können.

    Args:
        params: Beschreibung des Fotos (was ist zu sehen?) und Foto-Qualität

    Returns:
        Strukturierte Analyse: Kategorie, Marke, Zustand, Defekte, Empfehlungen.
    """
    desc = params.photo_description.lower()

    # ── Kategorie-Erkennung (Keyword-basiert v1) ──
    category_keywords = {
        "Elektronik": ["smartphone", "handy", "laptop", "tablet", "kopfhörer", "kabel",
                        "ladegerät", "monitor", "tastatur", "maus", "konsole", "controller",
                        "fernseher", "tv", "kamera", "drucker", "lautsprecher", "router"],
        "Kleidung": ["jacke", "hose", "hemd", "kleid", "schuhe", "sneaker", "pullover",
                      "mantel", "rucksack", "tasche", "mütze", "handschuhe", "fjällräven",
                      "kånken", "shirt", "t-shirt", "jeans", "stiefel", "bluse"],
        "Möbel": ["regal", "tisch", "stuhl", "schrank", "bett", "sofa", "couch",
                   "kommode", "ikea", "kallax", "billy", "lampe", "spiegel", "matratze"],
        "Sport": ["fahrrad", "mountainbike", "yogamatte", "hanteln", "fitness", "ski",
                   "skateboard", "helm", "ball", "schläger", "laufschuhe", "trainingsgerät"],
        "Haushalt": ["toaster", "mixer", "kaffeemaschine", "staubsauger", "bügeleisen",
                      "pfanne", "topf", "geschirr", "besteck", "wasserkocher", "mikrowelle"],
        "Bücher": ["buch", "roman", "sachbuch", "lehrbuch", "comic", "manga", "zeitschrift"],
        "Kinder": ["spielzeug", "kinderwagen", "lego", "puppe", "kinderbuch", "hochstuhl",
                    "babykleidung", "schultasche", "ranzen"],
    }

    detected_category = "Sonstiges"
    detected_subcategory = None
    category_confidence = 0.5

    for cat, keywords in category_keywords.items():
        matches = [kw for kw in keywords if kw in desc]
        if matches:
            detected_category = cat
            detected_subcategory = matches[0].capitalize()
            category_confidence = min(0.6 + len(matches) * 0.1, 0.95)
            break

    # ── Marken-Erkennung (v1: einfacher Keyword-Check) ──
    known_brands = [
        "ikea", "fjällräven", "nike", "adidas", "samsung", "apple", "sony",
        "bosch", "siemens", "philips", "braun", "tefal", "wmf", "lego",
        "playmobil", "patagonia", "north face", "puma", "new balance",
        "vaude", "deuter", "ortlieb", "canyon", "cube", "specialized",
    ]
    detected_brand = None
    for brand in known_brands:
        if brand in desc:
            detected_brand = brand.title()
            break

    # ── Zustands-Einschätzung ──
    defect_indicators = {
        "kratzer": "Kratzer sichtbar",
        "riss": "Riss/Beschädigung",
        "fleck": "Flecken vorhanden",
        "abnutzung": "Abnutzungsspuren",
        "gebrauchssp": "Gebrauchsspuren",
        "defekt": "Defekt vorhanden",
        "kaputt": "Beschädigt",
        "löch": "Löcher vorhanden",
        "vergilb": "Vergilbung",
        "rost": "Rostspuren",
        "delle": "Dellen vorhanden",
    }
    detected_defects = []
    for indicator, label in defect_indicators.items():
        if indicator in desc:
            detected_defects.append(label)

    # Zustand ableiten
    positive_words = ["neu", "originalverpack", "unbenutzt", "ovp", "versiegelt"]
    like_new_words = ["wie neu", "kaum benutzt", "fast neu", "top zustand"]
    good_words = ["gut", "funktioniert", "intakt", "gepflegt"]

    if any(w in desc for w in positive_words):
        condition = "neu"
        condition_confidence = 0.85
    elif any(w in desc for w in like_new_words):
        condition = "wie_neu"
        condition_confidence = 0.80
    elif len(detected_defects) >= 3:
        condition = "akzeptabel"
        condition_confidence = 0.70
    elif detected_defects:
        condition = "gut"
        condition_confidence = 0.70
    elif any(w in desc for w in good_words):
        condition = "gut"
        condition_confidence = 0.75
    else:
        condition = "gut"
        condition_confidence = 0.60

    # ── Foto-Qualität ──
    photo_quality = params.photo_quality or "medium"

    # ── Ergebnis ──
    result = {
        "category": detected_category,
        "subcategory": detected_subcategory,
        "category_confidence": category_confidence,
        "brand": detected_brand,
        "condition": condition,
        "condition_confidence": condition_confidence,
        "defects": detected_defects,
        "photo_quality": photo_quality,
        "analysis_version": "v1-text",
        "photo_tips": [],
    }

    if photo_quality == "low":
        result["photo_tips"] = [
            "Bessere Beleuchtung verwenden (Tageslicht ideal)",
            "Produkt auf neutralem Hintergrund platzieren",
            "Mehrere Winkel fotografieren",
        ]

    # Markdown Output
    brand_str = f"**{detected_brand}**" if detected_brand else "nicht erkannt"
    defects_str = ", ".join(detected_defects) if detected_defects else "keine erkannt"

    lines = [
        "## SellBuddy Foto-Analyse\n",
        f"- **Kategorie:** {detected_category}"
        + (f" > {detected_subcategory}" if detected_subcategory else "")
        + f" (Konfidenz: {category_confidence:.0%})",
        f"- **Marke:** {brand_str}",
        f"- **Zustand:** {condition} (Konfidenz: {condition_confidence:.0%})",
        f"- **Erkannte Defekte:** {defects_str}",
        f"- **Foto-Qualität:** {photo_quality}",
        f"- **Analyse-Version:** v1 (Textbasiert)",
    ]

    if result["photo_tips"]:
        lines.append("\n### Foto-Tipps")
        for tip in result["photo_tips"]:
            lines.append(f"- {tip}")

    lines.append(f"\n```json\n{to_json(result)}\n```")

    return "\n".join(lines)


# ── Tool 8: SellBuddy – Generate Listing ─────────────────────────────────

class GenerateListingInput(BaseModel):
    title_hint: Optional[str] = Field(
        default=None,
        description="Optionaler Titelhinweis vom User (z.B. 'Mein alter Rucksack')",
    )
    category: str = Field(
        ..., description="Kategorie aus analyze_photo (z.B. 'Kleidung')"
    )
    subcategory: Optional[str] = Field(
        default=None, description="Unterkategorie (z.B. 'Rucksack')"
    )
    brand: Optional[str] = Field(
        default=None, description="Erkannte Marke (z.B. 'Fjällräven')"
    )
    condition: str = Field(
        default="gut", description="Zustand: neu, wie_neu, gut, akzeptabel"
    )
    defects: Optional[list[str]] = Field(
        default=None, description="Liste erkannter Defekte"
    )
    city: str = Field(
        default="Hannover-Mitte",
        description="Stadtteil (z.B. 'Hannover-Linden-Nord')",
    )
    seller_id: int = Field(..., description="Seller-ID des Verkäufers")
    description_extra: Optional[str] = Field(
        default=None,
        description="Zusätzliche Details vom User (Maße, Alter, Besonderheiten)",
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def remarket_generate_listing(params: GenerateListingInput) -> str:
    """Generiere ein optimiertes Listing aus einer Foto-Analyse.

    Erstellt SEO-Titel, strukturierte Beschreibung, Tags und Preisvorschlag.
    Das Listing wird direkt in die Datenbank geschrieben (Status: active).

    Workflow: remarket_analyze_photo → remarket_generate_listing → fertig!

    Args:
        params: Analysedaten (Kategorie, Marke, Zustand) + Seller-ID + Extras

    Returns:
        Das erstellte Listing mit ID, Titel, Preis und allen Details.
    """
    # ── SEO-Titel generieren ──
    parts = []
    if params.brand:
        parts.append(params.brand)
    if params.subcategory:
        parts.append(params.subcategory)
    elif params.title_hint:
        parts.append(params.title_hint)
    else:
        parts.append(params.category)

    condition_labels = {
        "neu": "NEU",
        "wie_neu": "Wie Neu",
        "gut": "Gut erhalten",
        "akzeptabel": "Mit Gebrauchsspuren",
    }
    parts.append(f"– {condition_labels.get(params.condition, params.condition)}")

    city_short = params.city.replace("Hannover-", "")
    parts.append(f"| {city_short}")
    title = " ".join(parts)

    # ── Beschreibung ──
    desc_lines = []
    if params.brand:
        desc_lines.append(f"Marke: {params.brand}")
    desc_lines.append(f"Zustand: {condition_labels.get(params.condition, params.condition)}")
    if params.defects:
        desc_lines.append(f"Hinweis: {', '.join(params.defects)}")
    if params.description_extra:
        desc_lines.append(params.description_extra)
    desc_lines.append(f"Standort: {params.city}")
    desc_lines.append("Versand oder Abholung möglich.")
    description = "\n".join(desc_lines)

    # ── Tags generieren ──
    tags = set()
    if params.brand:
        tags.add(params.brand.lower())
    if params.subcategory:
        tags.add(params.subcategory.lower())
    tags.add(params.category.lower())
    tags.add(params.condition)
    tags.add("hannover")
    tags.add(city_short.lower())
    tags.add("secondhand")
    if params.brand and params.brand.lower() in [
        "fjällräven", "patagonia", "vaude", "deuter", "ortlieb"
    ]:
        tags.add("europäisch")
        tags.add("nachhaltig")
    tags_list = sorted(tags)

    # ── Preisvorschlag via Marktdaten ──
    price_conditions = ["status = 'active'", "category = %s"]
    price_values: list = [params.category]
    if params.subcategory:
        price_conditions.append("subcategory ILIKE %s")
        price_values.append(f"%{params.subcategory}%")

    price_where = " AND ".join(price_conditions)
    stats = query_one(
        f"""SELECT avg(price_cents) as avg_price,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY price_cents) as median_price,
                   count(*) as count
            FROM remarket_listings WHERE {price_where}""",
        tuple(price_values),
    )

    if stats and stats["count"] > 0:
        base_price = float(stats["median_price"])
        # Zustandsanpassung
        condition_factor = {
            "neu": 1.15,
            "wie_neu": 1.0,
            "gut": 0.85,
            "akzeptabel": 0.65,
        }
        factor = condition_factor.get(params.condition, 0.85)
        suggested_cents = int(base_price * factor)
        price_source = f"Median {float(stats['median_price'])/100:.2f}€ × {factor} ({stats['count']} Vergleiche)"
    else:
        suggested_cents = 1500  # Default 15€
        price_source = "Standardpreis (keine Vergleichsdaten)"

    # ── In DB schreiben ──
    is_european = params.brand and params.brand.lower() in [
        "fjällräven", "patagonia", "vaude", "deuter", "ortlieb", "ikea", "bosch",
        "siemens", "wmf", "braun", "tefal", "philips",
    ]
    is_sustainable = "nachhaltig" in tags_list or "europäisch" in tags_list

    new_listing = insert_returning(
        """INSERT INTO remarket_listings
           (title, description, category, subcategory, price_cents, condition,
            city, region, seller_id, status, images_count, listing_quality_score,
            is_european_product, is_sustainable,
            sellbuddy_generated, sellbuddy_confidence, original_photo_quality,
            detected_brand, detected_defects, suggested_price_cents, tags)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',1,7,%s,%s,
                   true,0.75,'medium',%s,%s,%s,%s)
           RETURNING id, title, price_cents, category, condition, created_at""",
        (
            title, description, params.category, params.subcategory,
            suggested_cents, params.condition,
            params.city, "Niedersachsen", params.seller_id,
            is_european, is_sustainable,
            params.brand, params.defects or [], suggested_cents, tags_list,
        ),
    )

    if not new_listing:
        return "Fehler: Listing konnte nicht erstellt werden."

    price_eur = new_listing["price_cents"] / 100

    return (
        f"## SellBuddy – Listing erstellt! 🎉\n\n"
        f"**#{new_listing['id']}** {new_listing['title']}\n\n"
        f"- **Preis:** {price_eur:.2f} € ({price_source})\n"
        f"- **Kategorie:** {params.category}"
        + (f" > {params.subcategory}" if params.subcategory else "")
        + f"\n- **Zustand:** {params.condition}\n"
        f"- **Tags:** {', '.join(tags_list)}\n"
        f"- **Beschreibung:**\n{description}\n\n"
        f"Das Listing ist jetzt **live** auf ReMarket! "
        f"Nutze `remarket_get_listing` mit ID {new_listing['id']} für Details."
    )


# ── Tool 9: SellBuddy – Auto Reply ──────────────────────────────────────

class AutoReplyInput(BaseModel):
    listing_id: int = Field(..., description="ID des Listings")
    buyer_message: str = Field(
        ...,
        description="Nachricht des Käufers (z.B. 'Ist der Preis verhandelbar?')",
    )
    seller_style: str = Field(
        default="freundlich",
        description="Antwort-Stil: freundlich, sachlich, locker",
    )
    min_price_eur: Optional[float] = Field(
        default=None,
        description="Minimaler Preis für Verhandlungen (in EUR). Wird nicht offengelegt.",
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def remarket_auto_reply(params: AutoReplyInput) -> str:
    """Generiere eine automatische Antwort auf eine Käufer-Anfrage.

    SellBuddy antwortet im Stil des Verkäufers auf häufige Fragen:
    Preisverhandlung, Verfügbarkeit, Versand, Zustand.
    Die Konversation wird in der DB gespeichert.

    Args:
        params: Listing-ID, Käufer-Nachricht und Antwort-Stil

    Returns:
        Antwort-Vorschlag mit Typ und Konfidenz.
    """
    # Listing laden
    listing = query_one(
        """SELECT l.title, l.price_cents, l.condition, l.city, l.description,
                  l.category, l.detected_brand, l.tags,
                  s.username as seller_name
           FROM remarket_listings l
           JOIN remarket_sellers s ON l.seller_id = s.id
           WHERE l.id = %s AND l.status = 'active'""",
        (params.listing_id,),
    )

    if not listing:
        return f"Listing #{params.listing_id} nicht gefunden oder nicht aktiv."

    msg = params.buyer_message.lower()
    price_eur = listing["price_cents"] / 100

    # ── Nachrichtentyp erkennen ──
    reply_type = "custom"
    reply = ""
    confidence = 0.7

    style_greeting = {
        "freundlich": "Hallo! 😊",
        "sachlich": "Guten Tag,",
        "locker": "Hey!",
    }
    style_closing = {
        "freundlich": "Liebe Grüße! 😊",
        "sachlich": "Mit freundlichen Grüßen",
        "locker": "Cheers! ✌️",
    }
    greeting = style_greeting.get(params.seller_style, "Hallo!")
    closing = style_closing.get(params.seller_style, "Grüße!")

    if any(w in msg for w in ["preis", "verhandel", "rabatt", "weniger", "günstiger",
                                "letzte preis", "vhb", "was letzte"]):
        reply_type = "price_negotiation"
        if params.min_price_eur and price_eur > params.min_price_eur:
            discount = price_eur * 0.9  # 10% Rabatt anbieten
            discount = max(discount, params.min_price_eur)
            reply = (
                f"{greeting} Danke für dein Interesse an \"{listing['title']}\"! "
                f"Der Preis ist {price_eur:.2f} €. Ich könnte dir bei "
                f"{discount:.2f} € entgegenkommen. {closing}"
            )
            confidence = 0.85
        else:
            reply = (
                f"{greeting} Danke für die Anfrage! Der Preis von "
                f"{price_eur:.2f} € für \"{listing['title']}\" ist fair kalkuliert "
                f"und leider nicht mehr verhandelbar. {closing}"
            )
            confidence = 0.80

    elif any(w in msg for w in ["verfügbar", "noch da", "noch haben", "reserv"]):
        reply_type = "availability"
        reply = (
            f"{greeting} Ja, \"{listing['title']}\" ist noch verfügbar! "
            f"Abholung in {listing['city']} oder Versand – wie passt es dir? {closing}"
        )
        confidence = 0.90

    elif any(w in msg for w in ["versand", "liefern", "schicken", "post", "dhl"]):
        reply_type = "shipping"
        reply = (
            f"{greeting} Klar, Versand ist möglich! Ich verschicke versichert "
            f"per DHL (Paket ca. 4,99-6,99 € je nach Größe). "
            f"Abholung in {listing['city']} geht natürlich auch. {closing}"
        )
        confidence = 0.80

    elif any(w in msg for w in ["zustand", "mangel", "defekt", "kratzer", "kaputt",
                                  "funktioniert"]):
        reply_type = "condition_inquiry"
        cond_desc = {
            "neu": "komplett neu und unbenutzt",
            "wie_neu": "wie neu, kaum benutzt",
            "gut": "in gutem Zustand mit normalen Gebrauchsspuren",
            "akzeptabel": "funktionsfähig mit sichtbaren Gebrauchsspuren",
        }
        reply = (
            f"{greeting} \"{listing['title']}\" ist "
            f"{cond_desc.get(listing['condition'], listing['condition'])}. "
        )
        if listing.get("detected_brand"):
            reply += f"Marke: {listing['detected_brand']}. "
        reply += f"Bei Fragen melde dich gerne! {closing}"
        confidence = 0.80

    elif any(w in msg for w in ["maße", "größe", "dimension", "gewicht", "farbe"]):
        reply_type = "details"
        reply = (
            f"{greeting} Die Details findest du in der Beschreibung. "
            f"Falls was fehlt, frag gerne nochmal konkret – "
            f"ich messe/wiege gerne nach! {closing}"
        )
        confidence = 0.65

    else:
        reply_type = "custom"
        reply = (
            f"{greeting} Danke für deine Nachricht zu \"{listing['title']}\"! "
            f"Das Produkt ist in {listing['city']} verfügbar zum Preis von "
            f"{price_eur:.2f} €. Wie kann ich dir weiterhelfen? {closing}"
        )
        confidence = 0.55

    # In DB speichern
    conv = insert_returning(
        """INSERT INTO remarket_sellbuddy_conversations
           (listing_id, buyer_message, sellbuddy_reply, reply_type, confidence)
           VALUES (%s, %s, %s, %s, %s)
           RETURNING id, created_at""",
        (params.listing_id, params.buyer_message, reply, reply_type, confidence),
    )

    conv_id = conv["id"] if conv else "?"

    return (
        f"## SellBuddy Auto-Reply #{conv_id}\n\n"
        f"**Listing:** #{params.listing_id} – {listing['title']}\n"
        f"**Käufer schreibt:** {params.buyer_message}\n\n"
        f"### Antwort-Vorschlag ({reply_type}, Konfidenz: {confidence:.0%}):\n\n"
        f"> {reply}\n\n"
        f"*Stil: {params.seller_style} | Noch nicht vom Verkäufer bestätigt.*"
    )


# ── Tool 10: SellBuddy – Optimize Listing ───────────────────────────────

class OptimizeListingInput(BaseModel):
    listing_id: int = Field(..., description="ID des zu optimierenden Listings")
    apply_changes: bool = Field(
        default=False,
        description="True = Änderungen direkt anwenden. False = nur Vorschläge zeigen.",
    )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def remarket_optimize_listing(params: OptimizeListingInput) -> str:
    """Analysiere ein bestehendes Listing und schlage Verbesserungen vor.

    Prüft Titel (SEO), Beschreibung (Vollständigkeit), Preis (Marktvergleich),
    Tags und Foto-Qualität. Optional werden Änderungen direkt angewendet.

    Args:
        params: Listing-ID und ob Änderungen angewendet werden sollen

    Returns:
        Verbesserungsvorschläge mit aktuellem und vorgeschlagenem Qualitätsscore.
    """
    listing = query_one(
        """SELECT l.*, s.username, s.trust_score
           FROM remarket_listings l
           JOIN remarket_sellers s ON l.seller_id = s.id
           WHERE l.id = %s""",
        (params.listing_id,),
    )

    if not listing:
        return f"Listing #{params.listing_id} nicht gefunden."

    suggestions = []
    new_score = listing["listing_quality_score"] or 5
    updates = {}

    # ── Titel-Check ──
    title = listing["title"] or ""
    if len(title) < 15:
        suggestions.append(("Titel zu kurz", "Mindestens 15 Zeichen für SEO-Sichtbarkeit", +1))
    if listing.get("detected_brand") and listing["detected_brand"].lower() not in title.lower():
        new_title = f"{listing['detected_brand']} {title}"
        suggestions.append(("Marke fehlt im Titel", f"Vorschlag: \"{new_title}\"", +1))
        updates["title"] = new_title
    if listing.get("condition") and listing["condition"] not in title.lower():
        condition_labels = {"neu": "NEU", "wie_neu": "Wie Neu", "gut": "Gut erhalten", "akzeptabel": "Gebraucht"}
        label = condition_labels.get(listing["condition"], "")
        if label and label.lower() not in title.lower():
            suggestions.append(("Zustand fehlt im Titel", f"Zustand \"{label}\" ergänzen", +0.5))

    # ── Beschreibung ──
    desc = listing["description"] or ""
    if len(desc) < 50:
        suggestions.append(("Beschreibung zu kurz", "Mind. 50 Zeichen für Käufer-Vertrauen", +1))
    if "versand" not in desc.lower() and "abholung" not in desc.lower():
        suggestions.append(("Versand/Abholung fehlt", "Angabe ob Versand oder Abholung möglich", +0.5))

    # ── Preis-Check ──
    price_cents = listing["price_cents"]
    stats = query_one(
        """SELECT avg(price_cents) as avg_price,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY price_cents) as median,
                  count(*) as cnt
           FROM remarket_listings
           WHERE category = %s AND status = 'active' AND id != %s""",
        (listing["category"], listing["id"]),
    )
    if stats and stats["cnt"] >= 3:
        median = float(stats["median"])
        diff_pct = ((price_cents - median) / median) * 100 if median > 0 else 0
        if diff_pct > 30:
            suggestions.append(
                ("Preis deutlich über Markt",
                 f"Dein Preis: {price_cents/100:.2f}€, Median: {median/100:.2f}€ ({diff_pct:+.0f}%)", 0)
            )
        elif diff_pct < -30:
            suggestions.append(
                ("Preis unter Markt – evtl. erhöhen?",
                 f"Dein Preis: {price_cents/100:.2f}€, Median: {median/100:.2f}€ ({diff_pct:+.0f}%)", 0)
            )

    # ── Tags ──
    tags = listing.get("tags") or []
    if len(tags) < 3:
        auto_tags = {listing["category"].lower(), "hannover", "secondhand"}
        if listing.get("detected_brand"):
            auto_tags.add(listing["detected_brand"].lower())
        if listing.get("subcategory"):
            auto_tags.add(listing["subcategory"].lower())
        new_tags = sorted(auto_tags | set(tags))
        suggestions.append(("Zu wenige Tags", f"Vorschlag: {', '.join(new_tags)}", +0.5))
        updates["tags"] = new_tags

    # ── Bilder ──
    if listing["images_count"] < 3:
        suggestions.append(("Wenige Bilder", "3+ Bilder erhöhen die Verkaufschance um 40%", +1))

    # Score berechnen
    potential_boost = sum(s[2] for s in suggestions)
    optimized_score = min(10, new_score + potential_boost)

    # ── Optional: Änderungen anwenden ──
    applied = False
    if params.apply_changes and updates:
        set_parts = []
        values = []
        for k, v in updates.items():
            set_parts.append(f"{k} = %s")
            values.append(v)
        set_parts.append("listing_quality_score = %s")
        values.append(min(10, int(optimized_score)))
        values.append(params.listing_id)

        execute(
            f"UPDATE remarket_listings SET {', '.join(set_parts)} WHERE id = %s",
            tuple(values),
        )
        applied = True

    # ── Output ──
    lines = [
        f"## SellBuddy Listing-Optimierung #{params.listing_id}\n",
        f"**{listing['title']}** | {listing['category']} | {price_cents/100:.2f}€\n",
        f"**Aktueller Score:** {new_score}/10 → **Potenzial:** {optimized_score:.1f}/10\n",
    ]

    if not suggestions:
        lines.append("✅ Keine Verbesserungen nötig – das Listing ist top!")
    else:
        lines.append(f"### {len(suggestions)} Verbesserungsvorschläge\n")
        for title_s, detail, boost in suggestions:
            boost_str = f" (+{boost})" if boost > 0 else ""
            lines.append(f"- **{title_s}**{boost_str}: {detail}")

    if applied:
        lines.append(f"\n✅ **Änderungen angewendet!** Neuer Score: {int(optimized_score)}/10")
    elif updates and not params.apply_changes:
        lines.append(
            "\n*Nutze `apply_changes: true` um die Vorschläge automatisch anzuwenden.*"
        )

    return "\n".join(lines)


# ── Entry Point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
