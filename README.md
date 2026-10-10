# Oilers Feed

Erzeugt automatisch fertige deutsche Posts (Ergebnisse, Spieler-Facts, News) für einen WhatsApp-Kanal.
Die Posts erscheinen auf einer Webseite; dort tippst du auf **Teilen** und wählst in WhatsApp den Kanal.

## Einrichten
1. GitHub: Settings → Pages → Source: **GitHub Actions**.
2. Settings → Secrets → Actions → `ANTHROPIC_API_KEY` anlegen (ohne Key gibt es nur einfache englische Vorlagen).
3. Actions → "Oilers Feed" → Run workflow (danach alle 2 Stunden automatisch).
4. Die Pages-URL auf dem Handy öffnen und "Zum Startbildschirm hinzufügen".

Quellen/Einstellungen: `config.json`. Lokal testen: `python3 generate.py`.
