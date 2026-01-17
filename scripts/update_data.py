import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_PATH = os.path.join(REPO_ROOT, "data.json")

# ESPN "site" scoreboards (unofficial, but widely used)
ENDPOINTS = {
    "NFL":  "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "NHL":  "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
    "MLB":  "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
    # College football: groups=80 = FBS (helps return more than top-25)
    "NCAA": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=80",
}

def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "office-ticker/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

def yyyymmdd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")

def build_items_from_scoreboard(scoreboard: dict, league_label: str, mode: str) -> list[dict]:
    """
    mode:
      - "today": include scheduled/in-progress games for that date
      - "finals": include final games for that date
    """
    items = []
    for ev in scoreboard.get("events", [])[:200]:
        name = ev.get("name") or ev.get("shortName") or ""
        competitions = ev.get("competitions") or []
        comp = competitions[0] if competitions else {}
        status = (comp.get("status") or {}).get("type") or {}
        state = (status.get("state") or "").lower()   # pre / in / post

        # Extract scoreline when available
        comps = comp.get("competitors") or []
        # ESPN sometimes orders home/away; we’ll just format "Away X — Home Y" if we can.
        if len(comps) >= 2:
            # Identify home/away if present
            home = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
            away = next((c for c in comps if c.get("homeAway") == "away"), comps[1])

            home_team = ((home.get("team") or {}).get("shortDisplayName")
                         or (home.get("team") or {}).get("displayName")
                         or "HOME")
            away_team = ((away.get("team") or {}).get("shortDisplayName")
                         or (away.get("team") or {}).get("displayName")
                         or "AWAY")

            home_score = home.get("score")
            away_score = away.get("score")
        else:
            home_team = away_team = ""
            home_score = away_score = None

        # Time string
        date_str = comp.get("date")  # ISO
        time_part = ""
        if date_str:
            try:
                # ESPN date is UTC ISO; convert to ET-ish label without heavy tz libs
                # We'll just show "7:15 PM ET" style by using UTC and labeling "ET" in UI if you want later.
                dt_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                # crude ET approximation: subtract 5 hours (good enough for display; DST not handled)
                dt_et = dt_utc + timedelta(hours=-5)
                time_part = dt_et.strftime("%-I:%M %p ET")
            except Exception:
                time_part = ""

        # Filters for each mode
        if mode == "finals" and state != "post":
            continue
        if mode == "today" and state == "post":
            continue

        if state == "post" and home_score is not None and away_score is not None:
            text = f"FINAL: {away_team} {away_score} — {home_team} {home_score}"
        elif state == "in" and home_score is not None and away_score is not None:
            text = f"LIVE: {away_team} {away_score} — {home_team} {home_score}"
        elif time_part and away_team and home_team:
            text = f"{away_team} @ {home_team} {time_part}"
        else:
            # fallback to event name
            text = name

        items.append({"league": league_label, "text": text})

    return items

def load_data() -> dict:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data: dict) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

def main():
    data = load_data()

    # Use UTC "today" but format dates for the ESPN endpoints
    now_utc = datetime.now(timezone.utc)
    today = now_utc
    yesterday = now_utc - timedelta(days=1)

    # Build new today/finals lists
    new_today = []
    new_finals = []

    for league, base_url in ENDPOINTS.items():
        # Add dates param if not already present
        if "?" in base_url:
            today_url = f"{base_url}&dates={yyyymmdd(today)}"
            yday_url = f"{base_url}&dates={yyyymmdd(yesterday)}"
        else:
            today_url = f"{base_url}?dates={yyyymmdd(today)}"
            yday_url = f"{base_url}?dates={yyyymmdd(yesterday)}"

        try:
            sb_today = fetch_json(today_url)
            sb_yday = fetch_json(yday_url)

            new_today.extend(build_items_from_scoreboard(sb_today, league, "today"))
            new_finals.extend(build_items_from_scoreboard(sb_yday, league, "finals"))
        except Exception:
            # If ESPN is flaky, don't wipe your existing data—just keep what you had
            continue

    # Only overwrite if we got *something* back (prevents blanking in off-season)
    if new_today:
        data["today"] = new_today[:200]
    if new_finals:
        data["finals"] = new_finals[:200]

    # OPTIONAL: update favorites by filtering today+finals for team names
    favs = []
    fav_map = data.get("favoritesTeams", {})
    for league_key, team_list in fav_map.items():
        # Look in today's games first, then finals
        candidates = (data.get("today", []) + data.get("finals", []))
        for t in team_list:
            hit = next((it for it in candidates
                        if it.get("league") == league_key and t.lower() in it.get("text", "").lower()), None)
            if hit:
                favs.append(hit)
            else:
                favs.append({"league": league_key, "text": f"{t} — no item today"})

    if favs:
        data["favorites"] = favs[:50]

    # Stamp status line
    data["statusLine"] = "NFL • NCAA FB • MLB • NHL — auto-updated (Today/Finals)"
    save_data(data)

if __name__ == "__main__":
    main()
