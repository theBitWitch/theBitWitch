#!/usr/bin/env python3
"""
crawl_stats.py – Öffentliche Social-Media-Kennzahlen für theBitWitch

Scrapt Social Blade (socialblade.com) für jede Plattform und schreibt
das Ergebnis in stats.json, das du dann in GitHub Pages committen kannst.

Setup (einmalig):
    pip install playwright
    playwright install chromium

Ausführen:
    python crawl_stats.py

Danach stats.json committen & pushen → Media Kit aktualisiert sich automatisch.

Hinweise:
  - HEADLESS = False zeigt das Browser-Fenster (empfohlen, falls CAPTCHAs auftauchen)
  - Die Follower-Zahlen kommen von Social Blade; für 30-Tage-Daten braucht Social Blade
    manchmal einen Moment zum Laden – daher die Wartezeiten.
  - Falls eine Plattform None zurückgibt, war sie nicht scrapebar (gesperrt/Timeout).
    Der Wert bleibt dann in stats.json auf null.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Windows-Konsolen nutzen oft cp1252 statt UTF-8 → die ✓/↳/⚠-Symbole
# unten würden mit UnicodeEncodeError abstürzen. Auf UTF-8 umschalten.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("❌ playwright nicht installiert. Bitte ausführen:")
    print("   pip install playwright")
    print("   playwright install chromium")
    sys.exit(1)

# ── Konfiguration ─────────────────────────────────────────────────────────────

HANDLES = {
    "twitch":    "thebitwitch",
    "instagram": "thebitwitch",
    "tiktok":    "thebitwitch",
    "youtube":   "the_bitwitch",
}

OUTPUT    = Path(__file__).parent / "stats.json"
HEADLESS  = os.environ.get("CI", "").lower() in ("true", "1")  # lokal sichtbar, in CI headless
TIMEOUT   = 35_000  # ms pro Seitenaufruf
WAIT_JS   = 4_000   # ms nach Laden warten (JS-gerenderte Inhalte)

SOCIALBLADE_URLS = {
    "twitch":    "https://socialblade.com/twitch/user/{handle}",
    "youtube":   "https://socialblade.com/youtube/channel/UCVN--Mt0Fw2_GpWKqHledmg",
    "instagram": "https://socialblade.com/instagram/user/{handle}",
    "tiktok":    "https://socialblade.com/tiktok/user/{handle}",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def parse_num(s: str) -> int | None:
    """
    Parst Zahlen wie '1.2K', '3,4 Mio', '123.456', '2M' → int
    Gibt None zurück bei nicht-parsbaren Strings.
    """
    if not s:
        return None
    s = s.strip()
    # Deutsches Format: Punkt = Tausender, Komma = Dezimal → normalisieren
    # Erst schauen ob K/M/B Suffix
    m = re.search(r'([\d][0-9]*(?:[.,]\d+)?)\s*([KkMmBb]?)', s.replace(' ', ''))
    if not m:
        return None
    num_str = m.group(1)
    suffix  = m.group(2).upper()
    # Komma → Punkt für float-Konvertierung
    num_str = num_str.replace(',', '.')
    # Falls kein Suffix: Punkt könnte Tausendertrennzeichen sein
    if not suffix and num_str.count('.') == 1 and len(num_str.split('.')[1]) == 3:
        num_str = num_str.replace('.', '')
    try:
        n = float(num_str)
    except ValueError:
        return None
    multipliers = {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}
    return int(n * multipliers.get(suffix, 1))


def find_num_after(text: str, *keywords, window: int = 300, min_val: int = 1, max_val: int = 100_000_000, year_filter: bool = True) -> int | None:
    """
    Sucht die erste Zahl im Text innerhalb von `window` Zeichen
    nach einem der angegebenen Keywords (case-insensitive).
    Ignoriert Jahres-ähnliche Werte (1900-2100) wenn year_filter=True.
    """
    text_lower = text.lower()
    for kw in keywords:
        idx = text_lower.find(kw.lower())
        if idx == -1:
            continue
        snippet = text[idx : idx + window]
        # Alle Zahlen mit optionalem K/M/B-Suffix finden und filtern
        for m in re.finditer(r'([\d][0-9,\.]*\s*[KkMmBb]?)', snippet):
            result = parse_num(m.group(1))
            if result is None:
                continue
            # Jahresfilter: 1900–2100 sind fast immer Jahreszahlen, keine Follower
            if year_filter and 1900 <= result <= 2100:
                continue
            if min_val <= result <= max_val:
                return result
    return None


# ── Scraper-Funktionen ────────────────────────────────────────────────────────

def find_float_after(text: str, *keywords, window: int = 120) -> float | None:
    """Wie find_num_after, gibt aber float zurück (für Prozentwerte / Dezimalzahlen)."""
    text_lower = text.lower()
    for kw in keywords:
        idx = text_lower.find(kw.lower())
        if idx == -1:
            continue
        snippet = text[idx : idx + window]
        m = re.search(r'(\d+(?:[.,]\d+)?)', snippet)
        if m:
            try:
                return float(m.group(1).replace(',', '.'))
            except ValueError:
                pass
    return None


def _is_blocked_page(text: str) -> bool:
    """
    Erkennt Cloudflare-Challenges & Block-Seiten anhand ihres Textes.
    Ohne diese Prüfung würde einfach ergebnislos nach Zahlen in der
    Challenge-Seite gesucht – der Grund für das Fehlen der Daten bliebe
    dann in den Logs unsichtbar.
    """
    t = text[:1000].lower()
    return any(marker in t for marker in (
        "sicherheitsüberprüfung wird durchgeführt",
        "checking your browser",
        "checking if the site connection is secure",
        "you have been blocked",
        "attention required! | cloudflare",
        "just a moment",
    ))


def _load_socialblade(page, url: str) -> str | None:
    """Lädt eine Social Blade Seite und gibt den Body-Text zurück, oder None bei Fehler."""
    try:
        page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_timeout(WAIT_JS)
        text = page.inner_text("body")
        # Social Blade zeigt "404" oder "not found" wenn der Kanal nicht existiert
        if "not found" in text.lower() or "404" in text[:500]:
            return None
        if _is_blocked_page(text):
            print("  ⚠ Von Cloudflare blockiert (Challenge-Seite)")
            return None
        return text
    except PWTimeout:
        return None
    except Exception:
        return None


def scrape_platform(page, platform: str, handle: str) -> dict:
    """
    Scrapt eine Plattform über Social Blade.
    Gibt ein Dict mit verfügbaren Kennzahlen zurück; nicht verfügbare = None.
    """
    stats = {}
    url = SOCIALBLADE_URLS[platform].format(handle=handle)
    print(f"  ↳ {url}")
    text = _load_socialblade(page, url)

    if not text:
        print(f"  ⚠ Keine Social Blade Daten für {platform}")
        return stats

    # Plattform-spezifische Extraktion
    if platform == "twitch":
        stats["followers"] = find_num_after(text, "Followers", "follower")

    elif platform == "youtube":
        stats["subscribers"] = find_num_after(text, "Subscribers", "Abonnenten")

    elif platform == "instagram":
        stats["followers"] = find_num_after(text, "Followers", "follower")

    elif platform == "tiktok":
        stats["followers"] = find_num_after(text, "Followers", "follower")
        stats["likes"]     = find_num_after(text, "Likes", "Hearts", "Like count")

    # Nullen durch None ersetzen (unplausible 0-Werte)
    for k, v in stats.items():
        if v == 0:
            stats[k] = None

    return stats


def scrape_twitchtracker(page, handle: str) -> dict:
    """
    Scrapt TwitchTracker für avg_viewers und top_percent.
    """
    url = f"https://twitchtracker.com/{handle}"
    print(f"  \u21b3 (TwitchTracker) {url}")
    result = {}
    try:
        page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        text = page.inner_text("body")
    except Exception as e:
        print(f"  \u26a0 TwitchTracker nicht erreichbar: {e}")
        return result

    if _is_blocked_page(text):
        print("  \u26a0 TwitchTracker: von Cloudflare blockiert (Challenge-Seite)")
        return result

    # Avg viewers: "Avg viewers \u25cf 49.2"
    m = re.search(r'Avg\s*viewers?\s*[\u25cf:\-]?\s*([\d]+(?:[.,]\d+)?)', text, re.IGNORECASE)
    if m:
        try:
            result["avg_viewers"] = round(float(m.group(1).replace(',', '.')), 1)
        except ValueError:
            pass

    # Top %: "Twitch Top 0.76%"
    m = re.search(r'Twitch\s+Top\s+([\d]+(?:[.,]\d+)?)%', text, re.IGNORECASE)
    if m:
        try:
            result["top_percent"] = round(float(m.group(1).replace(',', '.')), 2)
        except ValueError:
            pass

    return result


def scrape_beacons_platform(page, handle: str, platform: str) -> dict:
    """
    Scrapt beacons.ai/mediakit für eine Plattform (twitch/tiktok/youtube).
    Die Seite ist server-side gerendert, daher kein JS-Execution nötig.
    """
    if platform == "tiktok":
        url = f"https://beacons.ai/{handle}/mediakit"
    else:
        url = f"https://beacons.ai/{handle}/mediakit?platform={platform}"
    print(f"  ↳ (Beacons/{platform}) {url}")
    result = {}
    try:
        page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        text = page.inner_text("body")
    except Exception as e:
        print(f"  ⚠ Beacons/{platform} nicht erreichbar: {e}")
        return result

    if _is_blocked_page(text):
        print(f"  ⚠ Beacons/{platform}: von Cloudflare blockiert (Challenge-Seite)")
        return result

    if platform == "twitch":
        result["subs_twitch"]        = find_num_after(text, "SUBSCRIBERS", min_val=1, max_val=100_000)
        result["avg_stream_views"]   = find_num_after(text, "AVG STREAM VIEWS", min_val=1)
        result["avg_concurrent_30d"] = find_float_after(text, "AVG CONCURRENT VIEWERS")
        result["watch_hours_30d"]    = find_num_after(text, "WATCH HOURS", min_val=50, year_filter=False)

    elif platform == "tiktok":
        result["impressions_30d"]  = find_num_after(text, "TOTAL IMPRESSIONS", min_val=100)
        result["engagements_30d"]  = find_num_after(text, "TOTAL ENGAGEMENTS", min_val=1)
        result["avg_views_30d"]    = find_num_after(text, "AVG VIEWS", min_val=300, window=300, year_filter=False)
        result["avg_likes_30d"]    = find_num_after(text, "AVG LIKES", min_val=1)
        result["engagement_rate"]  = find_float_after(text, "ENGAGEMENT\n", "ENGAGEMENT ")


    elif platform == "youtube":
        result["impressions_30d"]    = find_num_after(text, "TOTAL IMPRESSIONS", min_val=100)
        result["engagements_30d"]    = find_num_after(text, "TOTAL ENGAGEMENTS", min_val=1)
        result["avg_shorts_views"]   = find_num_after(text, "AVG SHORTS VIEWS", min_val=1)
        result["engagement_rate"]    = find_float_after(text, "CONTENT ENGAGEMENT")

    # Nullen durch None ersetzen
    return {k: v for k, v in result.items() if v not in (None, 0)}


def with_fresh_page(browser, fn, *args, retries: int = 1, retry_delay: int = 8, **kwargs):
    """
    Führt fn(page, *args, **kwargs) in einem brandneuen Browser-Context aus
    (frische Cookies/Session) und schließt ihn danach wieder.

    Wichtig, weil Cloudflare (Social Blade, Beacons.ai) eine wiederverwendete
    Session nach der ersten Anfrage als Bot markiert und jede weitere Anfrage
    in dieser Session mit einer Challenge-Seite blockiert – auch wenn die
    Anfragen an unterschiedliche Domains gehen. Mit einem frischen Context
    pro Anfrage sieht jede Anfrage wie ein neuer Besucher aus.

    Liefert fn ein leeres Ergebnis (z.B. weil eine Challenge-Seite nur kurz
    aktiv war), wird es nach `retry_delay` Sekunden bis zu `retries`-mal mit
    einem erneut frischen Context wiederholt.
    """
    result = None
    for attempt in range(retries + 1):
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="de-DE",
        )
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = context.new_page()
        try:
            result = fn(page, *args, **kwargs)
        finally:
            context.close()
        if result:
            return result
        if attempt < retries:
            print(f"     … leeres Ergebnis, neuer Versuch in {retry_delay}s")
            time.sleep(retry_delay)
    return result


# ── Hauptprogramm ─────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  theBitWitch – Social-Media-Crawler")
    print("=" * 60)

    results = {}
    urls = {
        "twitch":    f"https://twitch.tv/{HANDLES['twitch']}",
        "youtube":   f"https://youtube.com/@{HANDLES['youtube']}",
        "instagram": f"https://instagram.com/{HANDLES['instagram']}",
        "tiktok":    f"https://tiktok.com/@{HANDLES['tiktok']}",
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        for platform in ["twitch", "youtube", "instagram", "tiktok"]:
            handle = HANDLES[platform]
            print(f"\n[{platform.upper()}] @{handle}")
            stats = with_fresh_page(browser, scrape_platform, platform, handle)

            if platform == "twitch":
                tt = with_fresh_page(browser, scrape_twitchtracker, HANDLES["twitch"])
                stats.update(tt)
                beacons = with_fresh_page(browser, scrape_beacons_platform, HANDLES["twitch"], "twitch")
                stats.update(beacons)

            if platform in ("tiktok", "youtube"):
                beacons = with_fresh_page(browser, scrape_beacons_platform, HANDLES["twitch"], platform)
                stats.update(beacons)

            stats["url"] = urls[platform]

            # Gefundene Werte ausgeben
            for k, v in stats.items():
                if k != "url":
                    status = "✓" if v is not None else "–"
                    print(f"    {status} {k}: {v}")

            results[platform] = stats

        browser.close()

    # stats.json schreiben – None-Werte werden nicht ausgegeben
    cleaned = {
        plat: {k: v for k, v in pdata.items() if v is not None}
        for plat, pdata in results.items()
    }

    # Schutz: nicht schreiben wenn zu wenige Daten (Scraper-Fehler in CI)
    # Mindestens 10 Werte (URL-Felder nicht mitzählen)
    real_values = sum(
        sum(1 for k in d if k != "url")
        for d in cleaned.values()
    )

    # Bestehende stats.json lesen um zu vergleichen
    existing_real = 0
    if OUTPUT.exists():
        try:
            with open(OUTPUT, encoding="utf-8") as f:
                existing = json.load(f)
            existing_real = sum(
                sum(1 for k in v if k not in ("url", "updated"))
                for v in existing.values()
                if isinstance(v, dict)
            )
        except Exception:
            pass

    if real_values < 8 or real_values < existing_real - 2:
        print(f"\n⚠️  Nur {real_values} Datenwerte gesammelt (vorher: {existing_real}) – stats.json wird NICHT überschrieben.")
        print("   Crawler-Fehler in CI? Prüfe die Action-Logs.")
        return

    output = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **cleaned,
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ stats.json geschrieben → {OUTPUT}")
    print("   Jetzt committen & pushen, damit das Media Kit aktualisiert wird.")


if __name__ == "__main__":
    main()
