import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_PATH = os.path.join(REPO_ROOT, "data.json")

ENDPOINTS = {
    "NFL":  "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "NHL":  "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
    "MLB":  "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
    # FBS (helps include more than top-25 when in season)
    "NCAA": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=80",
}

def fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "office-ticker/1.1",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

def yyyymmdd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")

def with_dates(url: str, dates_value: str) -> str:
    # Preserve existing query params
    if "?" in url:
        return f"{url}&dates={dates_value}"
    return f"{url}?dates={dates_value}"

def build_items(scoreboard: dict, league_label: str, mode: str) -> list[dict]:
    """
    mode:
      - "today": scheduled/in-progress (pre + in)
      - "finals": post
    """
    items = []
    events = scoreboard.get("events") or []
    for ev in events[:250]:
        comps = (ev.get("competitions") or [])
        comp = comps[0] if comps else {}
        status = (comp.get("status") or {}).get("type") or {}
        state = (status.get("state") or "").lower()  # pre / in / post

        if mode == "finals" and state != "post":
            continue
        if mode == "today" and state == "post":
            continue

        competitors = comp.get("competitors") or []
        if len(competitors) >= 2:
            home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
            away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

            home_team = ((home.get("team") or {}).get("shortDisplayName")
                         or (home.get("team") or {}).get("displayName")
                         or "HOME")
            away_team = ((away.get("team") or {}).get("shortDisplayName")
                         or (away.get("team") or {}).get("displayName")
                         or "AWAY")

            home_score = home.get("score")
            away_score = away.get("score")
        else:
            # fallback
            home_team = away_team = ""
            home_score = away_score = None

        # Time (we’ll print ET label; exact DST accuracy isn’t critical for a ticker)
        time_part = ""
        iso = comp.get("date")
        if iso:
            try:
                dt_utc = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                dt_et = dt_utc + timedelta(hours=-5)
                time_part = dt_et.strftime("%-I:%M %p ET")
            except Exception:
                time_part = ""

        if state == "post" and home_score is not None and away_score is not None:
            text = f"FINAL: {away_team} {away_score} — {home_team} {home_score}"
        elif state == "in" and home_score is not None and away_score is not None:
            text = f"LIVE: {away_team} {away_score} — {home_team} {home_score}"
        elif time_part and away_team and home_team:
            text = f"{away_team} @ {home_team} {time_part}"
        else:
            # fallback to event name
            text = ev.get("name") or ev.get("shortName") or "Game"

        items.append({"league": league_label, "text": text})

    return items

def text_has_team(text: str, team: str) -> bool:
    t = team.lower()
    s = text.lower()
    # word-ish boundary matching
    return f" {t} " in f" {s} " or s.startswith(f"{t} ")

def load_data() -> dict:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data: dict) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

def main():
    data = load_data()

    # Use "local" day as America/Chicago-ish so “today” matches you.
    # (Good enough without timezone libs)
    now_local = datetime.now(timezone.utc) + timedelta(hours=-6)
    today = now_local.date()
    yesterday = (now_local - timedelta(days=1)).date()
    tomorrow = (now_local + timedelta(days=1)).date()

    today_str = today.strftime("%Y%m%d")
    yday_str = yesterday.strftime("%Y%m%d")
    tmr_str = tomorrow.strftime("%Y%m%d")

    # Ranges help ESPN return expected results
    today_range = f"{today_str}-{tmr_str}"
    finals_range = f"{yday_str}-{today_str}"

    new_today = []
    new_finals = []

    for league, base_url in ENDPOINTS.items():
        try:
            # 1) Try date-range filtered
            url_today = with_dates(base_url, today_range)
            url_finals = with_dates(base_url, finals_range)

            sb_today = fetch_json(url_today)
            sb_finals = fetch_json(url_finals)

            items_today = build_items(sb_today, league, "today")
            items_finals = build_items(sb_finals, league, "finals")

            # 2) Fallback: if date-filter gives nothing, try without dates param
            if not items_today:
                print(f"[{league}] No TODAY items via dates range. Trying without dates…")
                sb_fallback = fetch_json(base_url)
                items_today = build_items(sb_fallback, league, "today")

            if not items_finals:
                # finals can legitimately be empty sometimes; no fallback needed
                pass

            print(f"[{league}] today_items={len(items_today)} finals_items={len(items_finals)}")
            new_today.extend(items_today)
            new_finals.extend(items_finals)

        except Exception as e:
            print(f"[{league}] ERROR fetching/parsing scoreboard: {e}")

    # Only overwrite if we got something (prevents blanking everything)
    if new_today:
        data["today"] = new_today[:250]
    else:
        print("No TODAY items found across leagues; keeping existing data['today'].")

    if new_finals:
        data["finals"] = new_finals[:250]
    else:
        print("No FINALS items found across leagues; keeping existing data['finals'].")

    # Favorites
    favs = []
    fav_map = data.get("favoritesTeams", {})
    candidates = (data.get("today", []) + data.get("finals", []))

    for league_key, teams in fav_map.items():
        for team in teams:
            hit = next(
                (it for it in candidates
                 if it.get("league") == league_key and text_has_team(it.get("text", ""), team)),
                None
            )
            if hit:
                favs.append(hit)
            else:
                favs.append({"league": league_key, "text": f"{team} — no game/result today"})

    data["favorites"] = favs[:60]

    # Stamp status
    data["statusLine"] = "NFL • NCAA FB • MLB • NHL — auto-updated"
    save_data(data)
    print("Wrote data.json successfully.")

if __name__ == "__main__":
    main()
