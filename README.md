# theBitWitch – Link in Bio

Persönliche Link-in-Bio-Website für [@theBitWitch](https://thebitwitch.github.io/theBitWitch), gehostet auf GitHub Pages.

## Was ist das hier?

Eine statische Website ohne Frameworks oder Build-Tools – nur HTML, CSS und ein bisschen JS. Enthält:

- **Startseite** (`index.html`) – Links zu allen Plattformen, Lost-Counter, QR-Code-Modal
- **Media Kit** (`mediakit.html`) – Automatisch aktualisierte Reichweiten-Stats, Zielgruppe und Kontakt für Kooperationen
- **Impressum & Datenschutz** – DSGVO-konform, nach deutschem Recht

## Automatische Stats

Ein Python-Skript (`crawl_stats.py`) scrapt täglich Follower- und Engagement-Zahlen von SocialBlade, TwitchTracker und beacons.ai und schreibt sie in `stats.json`. GitHub Actions führt das automatisch zweimal täglich aus.

```
.github/workflows/update-stats.yml   → läuft 10:00 und 21:00 Uhr (DE)
crawl_stats.py                        → Playwright-basierter Scraper
stats.json                            → Output, wird von mediakit.html geladen
```

### Lokal ausführen

```bash
pip install playwright
playwright install chromium
python crawl_stats.py
```

## Technisches

- Keine externen CDN-Abhängigkeiten – Fonts und Icons werden lokal ausgeliefert
- Dark Mode via CSS Custom Properties, gespeichert in `localStorage`
- QR-Code als statische PNG-Datei (`assets/qr.png`)

## Lizenz

[MIT](LICENSE) – Code frei verwendbar. Persönliche Inhalte, Bilder und Branding gehören theBitWitch.
