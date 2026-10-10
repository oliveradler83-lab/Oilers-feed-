#!/usr/bin/env python3
"""Erzeugt fertige deutsche WhatsApp-Posts über die Edmonton Oilers.

Quellen: NHL-API (Ergebnisse, Spieler-Facts) und RSS-News.
Ausgabe: docs/posts.json (+ docs/img/*), die von docs/index.html angezeigt wird.
Benötigt nur Python 3.9+; ANTHROPIC_API_KEY ist optional (ohne Key: einfache Vorlagen).
"""
import hashlib
import json
import os
import random
import traceback
import re
import urllib.request
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
POSTS_FILE = DOCS / "posts.json"
CFG = json.loads((ROOT / "config.json").read_text())
NHL = "https://api-web.nhle.com/v1"
UA = {"User-Agent": "oilers-feed/1.0"}

MAX_FACTS_PER_DAY = 3
MAX_QUIZ_PER_DAY = 1
MAX_NEWS_PER_RUN = 2
MAX_NEWS_PER_DAY = 5
NEWS_MAX_AGE_HOURS = 24
HEADERS = {
    "News": "📰 NEWS",
    "Spiel": "🏒 SPIELBERICHT",
    "Vorschau": "🔜 SPIELVORSCHAU",
    "Fact": "⭐ FACT DES TAGES",
    "Tabelle": "📊 TABELLE",
}
SEEN = set()      # IDs aller bereits erzeugten Posts
EXISTING = []     # bisherige Posts (für Tageslimits)


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def posts_today(kind):
    return [p for p in EXISTING if p["type"] == kind and p["created"].startswith(today())]


def get(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def get_json(url):
    return json.loads(get(url))


def pid(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


def save_image(url):
    """Lädt ein Bild herunter, damit die Seite es ohne CORS-Probleme teilen kann."""
    try:
        name = pid(url) + ".png"
        path = DOCS / "img" / name
        if not path.exists():
            path.write_bytes(get(url))
        return "img/" + name
    except Exception as e:
        print("Bild fehlgeschlagen:", e)
        return None


def ask_claude(prompt, fallback):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return fallback
    body = json.dumps({
        "model": CFG["model"],
        "max_tokens": 1500,
        "system": (
            "Du schreibst Posts für einen WhatsApp-Kanal von Edmonton-Oilers-Fans, "
            "betrieben von einem Jugendlichen. Schreibe auf Deutsch, locker, begeistert, "
            "maximal 450 Zeichen, 1-3 passende Emojis, am Ende 2-3 Hashtags (#Oilers #LetsGoOilers ...). "
            "Erfinde keine Fakten, nutze nur die gelieferten Informationen. "
            "Gib ausschließlich den fertigen Posttext aus."
        ),
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    try:
        out = json.loads(get(
            "https://api.anthropic.com/v1/messages", body,
            {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        ))
        text = "".join(b.get("text", "") for b in out["content"] if b.get("type") == "text").strip()
        if not text:
            raise ValueError(f"Antwort ohne Text: {str(out)[:300]}")
        return text
    except Exception as e:
        print("Claude-Fehler:", e)
        return fallback


def game_posts():
    posts = []
    sched = get_json(f"{NHL}/club-schedule-season/{CFG['team']}/now")
    finished = [g for g in sched.get("games", []) if g.get("gameState") in ("FINAL", "OFF")]
    for g in finished[-2:]:  # nur die letzten beiden Spiele
        h, a = g["homeTeam"], g["awayTeam"]
        line = f"{a['abbrev']} {a['score']} - {h['score']} {h['abbrev']}"
        home = h["abbrev"] == CFG["team"]
        mine, theirs = (h, a) if home else (a, h)
        result = "Sieg" if mine["score"] > theirs["score"] else "Niederlage"
        info = (f"Spielende am {g['gameDate']}: Die Edmonton Oilers haben {result} "
                f"{'zuhause' if home else 'auswärts'} gegen {team_name(theirs)} "
                f"mit {mine['score']}:{theirs['score']} (aus Oilers-Sicht).")
        if g.get("periodDescriptor", {}).get("periodType") in ("OT", "SO"):
            info += " Entscheidung nach Verlängerung/Shootout."
        text = ask_claude(f"Schreibe einen Ergebnis-Post. Infos: {info}", f"🏒 Endstand: {line}\n#Oilers #LetsGoOilers")
        posts.append({"id": pid("game", g["id"]), "type": "Spiel", "text": text, "image": None, "link": None})
    return posts


def fact_post():
    if len(posts_today("Fact")) >= MAX_FACTS_PER_DAY:
        return []
    stats = get_json(f"{NHL}/club-stats/{CFG['team']}/now")
    skaters = sorted(stats["skaters"], key=lambda s: s.get("points", 0), reverse=True)[:8]
    free = [x for x in skaters
            if pid("fact", f"{x['firstName']['default']} {x['lastName']['default']}", today()) not in SEEN]
    if not free:
        return []
    s = random.choice(free)
    name = f"{s['firstName']['default']} {s['lastName']['default']}"
    info = f"{name}: {s['gamesPlayed']} Spiele, {s['goals']} Tore, {s['assists']} Vorlagen, {s['points']} Punkte in dieser Saison."
    text = ask_claude(f"Schreibe einen kurzen Fun-Fact-Post. Infos: {info}", f"⭐ {info}\n#Oilers #LetsGoOilers")
    day = today()
    return [{"id": pid("fact", name, day), "type": "Fact", "text": text,
             "image": save_image(s["headshot"]) if s.get("headshot") else None, "link": None}]


def team_name(t):
    return f"{t['placeName']['default']} {t['commonName']['default']}"


def preview_post():
    """Vorschau auf das nächste Oilers-Spiel (Uhrzeit in deutscher Zeit)."""
    sched = get_json(f"{NHL}/club-schedule-season/{CFG['team']}/now")
    nxt = next((g for g in sched.get("games", []) if g.get("gameState") in ("FUT", "PRE")), None)
    if not nxt:
        return []
    start = datetime.fromisoformat(nxt["startTimeUTC"].replace("Z", "+00:00"))
    de = start.astimezone(ZoneInfo("Europe/Berlin"))
    h, a = nxt["homeTeam"], nxt["awayTeam"]
    home = h["abbrev"] == CFG["team"]
    opp = a if home else h
    info = (f"Nächstes Spiel: {de.strftime('%d.%m.%Y')} um {de.strftime('%H:%M')} Uhr deutscher Zeit, "
            f"{'Heimspiel' if home else 'Auswärtsspiel'} gegen {team_name(opp)}, Halle: {nxt['venue']['default']}.")
    text = ask_claude(f"Schreibe einen Vorfreude-Post zum nächsten Spiel mit Aufruf zum Mitfiebern. Infos: {info}",
                      f"🔜 {info}\n#Oilers #LetsGoOilers")
    return [{"id": pid("preview", nxt["id"]), "type": "Vorschau", "text": text, "image": None, "link": None}]


def standings_post():
    st = get_json(f"{NHL}/standings/now")["standings"]
    e = next(t for t in st if t["teamAbbrev"]["default"] == CFG["team"])
    info = (f"Oilers-Bilanz: {e['wins']} Siege, {e['losses']} Niederlagen, {e['otLosses']} OT-Niederlagen aus {e['gamesPlayed']} Spielen, "
            f"{e['points']} Punkte. Platz {e['divisionSequence']} in der {e['divisionName']} Division, "
            f"Platz {e['conferenceSequence']} in der {e['conferenceName']} Conference. Serie: {e['streakCount']}x {e['streakCode']}.")
    text = ask_claude(f"Schreibe einen kurzen Tabellen-Update-Post. Infos: {info}", f"📊 {info}\n#Oilers #LetsGoOilers")
    return [{"id": pid("standings", e["gamesPlayed"], e["points"]), "type": "Tabelle", "text": text, "image": None, "link": None}]


def quiz_post():
    """Quiz mit garantiert korrekter Lösung aus den Statistikdaten (nicht von der KI erfunden)."""
    if len(posts_today("Quiz")) >= MAX_QUIZ_PER_DAY:
        return []
    stats = get_json(f"{NHL}/club-stats/{CFG['team']}/now")["skaters"]
    stats = [s for s in stats if s.get("gamesPlayed", 0) > 0]
    if len(stats) < 4:
        return []
    name = lambda s: f"{s['firstName']['default']} {s['lastName']['default']}"
    key, label = random.choice([("goals", "die meisten Tore"), ("assists", "die meisten Vorlagen"), ("points", "die meisten Punkte")])
    ranked = sorted(stats, key=lambda s: s[key], reverse=True)
    if ranked[0][key] == ranked[1][key] or ranked[0][key] == 0:
        return []  # kein eindeutiger Sieger
    options = [ranked[0]] + random.sample(ranked[1:6], 3)
    random.shuffle(options)
    letters = "ABCD"
    lines = "\n".join(f"{letters[i]}) {name(o)}" for i, o in enumerate(options))
    right = letters[options.index(ranked[0])]
    day = today()
    q = f"🧠 OILERS-QUIZ\nWer hat aktuell {label} bei den Oilers in dieser Saison?\n\n{lines}\n\nAntwort in den Kanal-Reaktionen! 👇"
    a = f"✅ Auflösung: {right}) {name(ranked[0])} mit {ranked[0][key]}! 🔥\n#Oilers #LetsGoOilers"
    return [{"id": pid("quiz", day), "type": "Quiz", "text": q, "answer": a, "image": None, "link": None}]


def news_posts():
    posts = []
    budget = min(MAX_NEWS_PER_RUN, MAX_NEWS_PER_DAY - len(posts_today("News")))
    if budget <= 0:
        return posts
    for feed in CFG["news_feeds"]:
        try:
            root = ET.fromstring(get(feed))
        except Exception as e:
            print("Feed-Fehler:", e)
            continue
        for item in root.iter("item"):
            title = unescape(item.findtext("title", "")).strip()
            link = item.findtext("link", "").strip()
            if not title or not link or pid("news", link) in SEEN:
                continue
            try:
                age = datetime.now(timezone.utc) - parsedate_to_datetime(item.findtext("pubDate", ""))
                if age.total_seconds() > NEWS_MAX_AGE_HOURS * 3600:
                    continue
            except Exception:
                pass  # ohne Datum: trotzdem zulassen
            desc = re.sub(r"<[^>]+>", " ", unescape(item.findtext("description", "")))
            text = ask_claude(
                f"Fasse diese News in eigenen Worten als Post zusammen (nicht wörtlich übernehmen). Titel: {title}. Teaser: {desc[:300]}",
                f"📰 {title}\n#Oilers",
            )
            posts.append({"id": pid("news", link), "type": "News", "text": text + f"\n\nQuelle: {link}",
                          "image": None, "link": link})
            if len(posts) >= budget:
                break
    return posts


def main():
    DOCS.joinpath("img").mkdir(parents=True, exist_ok=True)
    existing = json.loads(POSTS_FILE.read_text()) if POSTS_FILE.exists() else []
    seen = {p["id"] for p in existing}
    SEEN.update(seen)
    EXISTING.extend(existing)
    new = []
    for fn in (game_posts, preview_post, quiz_post, fact_post, standings_post, news_posts):
        try:
            for p in fn():
                if p["id"] not in seen and len(new) < CFG["max_new_per_run"]:
                    p["created"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    if p["type"] in HEADERS:
                        p["text"] = HEADERS[p["type"]] + "\n\n" + p["text"]
                    EXISTING.append(p)
                    new.append(p)
                    seen.add(p["id"])
        except Exception as e:
            print(f"{fn.__name__} fehlgeschlagen:", repr(e)); traceback.print_exc()
    posts = (new + existing)[: CFG["keep_posts"]]
    POSTS_FILE.write_text(json.dumps(posts, ensure_ascii=False, indent=1))
    keep = {Path(p["image"]).name for p in posts if p.get("image")}
    for f in (DOCS / "img").glob("*"):
        if f.name not in keep:
            f.unlink()
    print(f"{len(new)} neue Posts, {len(posts)} gesamt")


if __name__ == "__main__":
    main()
