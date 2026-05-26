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
from datetime import datetime, timezone
from pathlib import Path

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
# Fallback-URLs für YouTube falls /user/ nicht existiert
SOCIALBLADE_YT_FALLBACKS = []

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


def find_num_after(text: str, *keywords, window: int = 300, min_val: int = 1, max_val: int = 100_000_000) -> int | None:
    """
    Sucht die erste Zahl im Text innerhalb von `window` Zeichen
    nach einem der angegebenen Keywords (case-insensitive).
    Ignoriert Jahres-ähnliche Werte (1900-2100) wenn min_val > 2100.
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
            if 1900 <= result <= 2100:
                continue
            if min_val <= result <= max_val:
                return result
    return None


def extract_30d_sum(text: str, section_keyword: str) -> int | None:
    """
    Versucht, die Summe der 30-Tage-Tabelle von Social Blade zu lesen.
    Social Blade zeigt am Tabellenende oft eine "30 Day Total"-Zeile.
    """
    # Suche nach "30 Day Total" oder "Monthly" Zusammenfassung
    total = find_num_after(
        text,
        "30 day total", "monthly total", "monatlich gesamt",
        window=200
    )
    if total is not None:
        return total
    # Fallback: Alle positiven Tageszahlen in der Nähe von section_keyword summieren
    idx = text.lower().find(section_keyword.lower())
    if idx == -1:
        return None
    block = text[idx : idx + 8000]
    # Finde alle Zahlen in der Tabelle (positive und negative Tageswerte)
    nums = re.findall(r'([+-]?[\d,\.]+)\s*[KkMmBb]?', block[:4000])
    daily = []
    for n in nums[:60]:  # Max 60 Einträge (2 Monate Puffer)
        try:
            v = int(float(n.replace(',', '').replace('+', '')))
            if -1_000_000 < v < 1_000_000:
                daily.append(v)
        except ValueError:
            pass
    if len(daily) >= 28:
        return sum(daily[:30])
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


def _load_socialblade(page, url: str) -> str | None:
    """Lädt eine Social Blade Seite und gibt den Body-Text zurück, oder None bei Fehler."""
    try:
        page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_timeout(WAIT_JS)
        text = page.inner_text("body")
        # Social Blade zeigt "404" oder "not found" wenn der Kanal nicht existiert
        if "not found" in text.lower() or "404" in text[:500]:
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
    urls_to_try = [SOCIALBLADE_URLS[platform].format(handle=handle)]

    # YouTube: mehrere URL-Varianten versuchen
    if platform == "youtube":
        urls_to_try += [u.format(handle=handle) for u in SOCIALBLADE_YT_FALLBACKS]

    text = None
    for url in urls_to_try:
        print(f"  ↳ {url}")
        text = _load_socialblade(page, url)
        if text:
            break

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

    if platform == "twitch":
        result["subs_twitch"]        = find_num_after(text, "SUBSCRIBERS", min_val=1, max_val=100_000)
        result["avg_stream_views"]   = find_num_after(text, "AVG STREAM VIEWS", min_val=1)
        result["avg_concurrent_30d"] = find_float_after(text, "AVG CONCURRENT VIEWERS")
        result["watch_hours_30d"]    = find_num_after(text, "WATCH HOURS", min_val=50)

    elif platform == "tiktok":
        result["impressions_30d"]  = find_num_after(text, "TOTAL IMPRESSIONS", min_val=100)
        result["engagements_30d"]  = find_num_after(text, "TOTAL ENGAGEMENTS", min_val=1)
        result["avg_views_30d"]    = find_num_after(text, "AVG VIEWS", min_val=1)
        result["avg_likes_30d"]    = find_num_after(text, "AVG LIKES", min_val=1)
        result["engagement_rate"]  = find_float_after(text, "ENGAGEMENT\n", "ENGAGEMENT ")

    elif platform == "youtube":
        result["impressions_30d"]    = find_num_after(text, "TOTAL IMPRESSIONS", min_val=100)
        result["engagements_30d"]    = find_num_after(text, "TOTAL ENGAGEMENTS", min_val=1)
        result["avg_shorts_views"]   = find_num_after(text, "AVG SHORTS VIEWS", min_val=1)
        result["engagement_rate"]    = find_float_after(text, "CONTENT ENGAGEMENT")

    # Nullen durch None ersetzen
    return {k: v for k, v in result.items() if v not in (None, 0)}


def scrape_youtube_videos_30d(page, handle: str) -> int | None:
    """
    Fallback: Scrapt den YouTube-Kanal-Videos-Tab und summiert Views
    von Videos der letzten 30 Tage.
    Gibt None zurück wenn nicht möglich.
    """
    url = f"https://www.youtube.com/@{handle}/videos"
    print(f"  ↳ (YouTube-Videos-Fallback) {url}")
    try:
        page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
        # Warten bis Videos geladen
        page.wait_for_selector("ytd-rich-item-renderer", timeout=15_000)
        page.wait_for_timeout(2_000)
    except Exception as e:
        print(f"  ⚠ YouTube-Videos nicht ladbar: {e}")
        return None

    # Video-Metadaten aus der Seite lesen
    text = page.inner_text("body")
    cutoff = datetime.now(timezone.utc)

    total_views = 0
    found = 0

    # Suche nach Mustern wie "vor 3 Wochen", "vor 2 Tagen", "vor 1 Monat"
    # und dazugehörigen Viewzahlen
    # YouTube zeigt: "1.234 Aufrufe", "vor 2 Wochen"
    patterns_age = [
        (r'vor\s+(\d+)\s+Stunde[n]?',  'hours'),
        (r'vor\s+(\d+)\s+Tag[e]?[n]?', 'days'),
        (r'vor\s+(\d+)\s+Woche[n]?',   'weeks'),
        (r'vor\s+(\d+)\s+Monat[e]?[n]?', 'months'),
        # Englisch (je nach User-Agent / Region)
        (r'(\d+)\s+hour[s]?\s+ago',    'hours'),
        (r'(\d+)\s+day[s]?\s+ago',     'days'),
        (r'(\d+)\s+week[s]?\s+ago',    'weeks'),
        (r'(\d+)\s+month[s]?\s+ago',   'months'),
    ]

    # Finde alle "(Zahl) Aufrufe" und "(Zeitangabe)" Paare
    view_matches = list(re.finditer(
        r'([\d][0-9,\.]*\s*[KkMmBb]?)\s*(?:Aufrufe|views?)',
        text, re.IGNORECASE
    ))

    for vm in view_matches:
        # Suche im Umfeld (1000 Zeichen) nach Zeitangabe
        start = max(0, vm.start() - 500)
        end   = min(len(text), vm.end() + 500)
        context = text[start:end]

        days_ago = None
        for pat, unit in patterns_age:
            m = re.search(pat, context, re.IGNORECASE)
            if m:
                n = int(m.group(1))
                if unit == 'hours':  days_ago = max(1, n // 24)
                elif unit == 'days': days_ago = n
                elif unit == 'weeks': days_ago = n * 7
                elif unit == 'months': days_ago = n * 30
                break

        if days_ago is not None and days_ago <= 30:
            v = parse_num(vm.group(1))
            if v and v > 0:
                total_views += v
                found += 1

    if found > 0:
        print(f"  ✓ YouTube: {found} Video(s) der letzten 30 Tage gefunden, {total_views:,} Views")
        return total_views
    return None

# ── Direkte Plattform-Scraper für Post-Views ─────────────────────────────────

def scrape_tiktok_views_30d(page, handle: str) -> int | None:
    """
    Scrapt das TikTok-Profil und summiert Views aller Posts der letzten 30 Tage.
    Interceptiert die interne API-Antwort, die TikTok nach dem Seitenload nachlädt.
    """
    url = f"https://www.tiktok.com/@{handle}"
    print(f"  ↳ (TikTok-Posts) {url}")

    captured = []

    def on_response(response):
        if any(kw in response.url for kw in ['item_list', 'aweme/v1', 'post/item', 'api/post']):
            try:
                data = response.json()
                if data:
                    captured.append(data)
            except Exception:
                pass

    page.on('response', on_response)
    try:
        page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        page.evaluate("window.scrollBy(0, 1200)")
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"  ⚠ TikTok nicht erreichbar: {e}")
        page.remove_listener('response', on_response)
        return None

    page.remove_listener('response', on_response)

    # Videos aus abgefangenen API-Antworten extrahieren
    videos = []
    for data in captured:
        items = data.get('itemList') or data.get('aweme_list') or []
        for v in items:
            if isinstance(v, dict) and 'createTime' in v:
                videos.append(v)

    if not videos:
        # DOM-Fallback: sichtbare View-Counts ohne Datumsfilter
        print("  ⚠ TikTok: API nicht interceptiert – DOM-Fallback")
        try:
            page.wait_for_selector('[data-e2e="user-post-item"]', timeout=8000)
            total = 0
            for item in page.locator('[data-e2e="user-post-item"]').all():
                try:
                    txt = item.inner_text()
                    m = re.search(r'([\d][0-9,\.]*\s*[KkMm]?)', txt)
                    if m:
                        v = parse_num(m.group(1))
                        if v and 100 < v < 100_000_000:
                            total += v
                except Exception:
                    pass
            if total > 0:
                print(f"  ~ TikTok: DOM-Fallback (kein Datumsfilter) → {total:,} Views")
                return total
        except Exception:
            pass
        print("  – TikTok: Keine Views ermittelbar")
        return None

    cutoff = datetime.now(timezone.utc).timestamp() - 30 * 86400
    total, count = 0, 0
    for video in videos:
        try:
            create_time = int(video.get('createTime', 0))
        except (ValueError, TypeError):
            continue
        if create_time < cutoff:
            continue
        stats = video.get('stats', video)
        play_count = stats.get('playCount', 0) or 0
        total += play_count
        count += 1

    if count > 0:
        print(f"  ✓ TikTok: {count} Post(s) ≤30 Tage → {total:,} Views")
        return total
    print("  – TikTok: Keine Posts der letzten 30 Tage gefunden")
    return None


def scrape_instagram_views_30d(page, handle: str) -> int | None:
    """
    Instagram sperrt ohne Login alle Post-Daten für Bots.
    Diese Funktion versucht es trotzdem, gibt aber None zurück wenn nötig.
    """
    print("  ⚠ Instagram: Post-Views ohne Login nicht abrufbar (Instagram blockiert Bots)")
    return None


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
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="de-DE",
        )
        # Automatisierungs-Fingerprint verbergen
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = context.new_page()

        for platform in ["twitch", "youtube", "instagram", "tiktok"]:
            handle = HANDLES[platform]
            print(f"\n[{platform.upper()}] @{handle}")
            stats = scrape_platform(page, platform, handle)

            if platform == "twitch":
                tt = scrape_twitchtracker(page, HANDLES["twitch"])
                stats.update(tt)
                beacons = scrape_beacons_platform(page, HANDLES["twitch"], "twitch")
                stats.update(beacons)

            if platform in ("tiktok", "youtube"):
                beacons = scrape_beacons_platform(page, HANDLES["twitch"], platform)
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
