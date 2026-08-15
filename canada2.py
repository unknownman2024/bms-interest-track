import asyncio
import aiohttp
import json
import html
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re

# ---------------- DATE LOGIC ---------------- #

# CINEPLEX_FILMS is now fetched dynamically – removed static mapping

TARGET_START="2026-08-25"

today=datetime.now(ZoneInfo("America/Toronto")).date()
target=datetime.fromisoformat(TARGET_START).date()

if today<target:
    SCH_DATE=target.isoformat()
else:
    SCH_DATE=(today+timedelta(days=3)).isoformat()

print("Tracking date:",SCH_DATE)

# ---------------- CONFIG ---------------- #

OMNI_VENUES_NOTNEEDED = []
OMNI_VENUES=[
 "https://omniwebticketing5.com/orleans/",
]
YORK_THEATRES=list(range(1,8))

# ---------- Convert to Cineplex format ---------- #

cine_date=datetime.fromisoformat(SCH_DATE)
CINEPLEX_DATE=f"{cine_date.month}%2F{cine_date.day}%2F{cine_date.year}"
print("Cineplex date:",CINEPLEX_DATE)

# (The json file for CINEPLEX_THEATRES is still needed)
with open("cineplexcanada.json") as f:
    CINEPLEX_THEATRES=json.load(f)["nearbyTheatres"]

HEADERS = {
    "accept": "*/*",
    "origin": "https://goldeneyecinemas.com",
    "referer": "https://goldeneyecinemas.com/",
    "user-agent": "Mozilla/5.0",
    "x-api-key": "FORYyLdL47yr:)QuAVytMvaYdfZIcYecwX"
}

CINE_HEADERS={
 "accept":"*/*",
 "ocp-apim-subscription-key":"dcdac5601d864addbc2675a2e96cb1f8",
 "origin":"https://www.cineplex.com",
 "referer":"https://www.cineplex.com/",
 "User-Agent":"Mozilla/5.0"
}

# ---------------- HTTP ---------------- #

async def fetch_json(session,url):
    for i in range(3):
        try:
            async with session.get(url) as r:
                return await r.json()
        except:
            await asyncio.sleep(2**i)
    return None

async def fetch(session,url):
    for i in range(3):
        try:
            async with session.get(url) as r:
                return await r.text()
        except:
            await asyncio.sleep(2**i)
    return None

# ---------------- OMNI ---------------- #

def extract_gmoviedata(html_text):
    match = re.search(r'var gMovieData\s*=\s*(\{.*?\});', html_text, re.DOTALL)
    if not match:
        return {}
    raw_json = match.group(1)
    clean = html.unescape(raw_json)
    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        print("\n[OMNI JSON ERROR]")
        print("Error:", e)
        print("Around error:")
        print(clean[e.pos-200:e.pos+200])
        return {}

async def scrape_omni(session):
    print("\n[OMNI] scanning venues")
    results=[]
    for base in OMNI_VENUES:
        url=f"{base}?schdate={SCH_DATE}"
        html_page=await fetch(session,url)
        if not html_page:
            continue
        movie_data=extract_gmoviedata(html_page)
        venue_name=base.rstrip("/").split("/")[-1]
        for movie in movie_data.values():
            title=movie["title"].strip()
            for aud in movie["schAuds"].values():
                for perf in aud["schPerfsReserved"].values():
                    results.append({
                     "venue":venue_name,
                     "movie":title,
                     "perfIx":perf["perfIx"],
                     "date":perf["schDateStr"],
                     "time":perf["startTimeStr"],
                     "total":0,
                     "available":0,
                     "blocked":0,
                     "sold":0,
                     "gross":0,
                     "gross_with_tax":0,
                     "per_ticket":{"net":0,"tax":0,"fee":0,"grand":0}
                    })
    print("[OMNI] shows:",len(results))
    return results

# ---------------- GOLDENEYE (REPLACEMENT FOR YORK) ---------------- #

GOLDEN_BASE = "https://backend.goldeneyecinemas.com"

GOLDEN_HEADERS = {
    "accept": "*/*",
    "origin": "https://goldeneyecinemas.com",
    "referer": "https://goldeneyecinemas.com/",
    "user-agent": "Mozilla/5.0",
    "x-api-key": "FORYyLdL47yr:)QuAVytMvaYdfZIcYecwX"
}

async def fetch_json_safe(session, url):
    for i in range(3):
        try:
            async with session.get(url) as r:
                if r.status != 200:
                    await asyncio.sleep(0.3)
                    continue
                return await r.json()
        except:
            await asyncio.sleep(2**i)
    return None

async def scrape_york(session):
    print("\n[GOLDENEYE] scanning theatres")
    results = []
    schedule = await fetch_json_safe(session, f"{GOLDEN_BASE}/schedule")
    if not schedule:
        print("[GOLDENEYE] failed to fetch schedule")
        return results
    tasks = []

    async def process_perf(theatre_name, theatre_id, film_name, showtime, performance_id):
        seat_url = f"{GOLDEN_BASE}/api/v1/theatres/{theatre_id}/performances/{performance_id}/seat-map"
        price_url = f"{GOLDEN_BASE}/api/v1/theatres/{theatre_id}/performances/{performance_id}/ticket-prices"
        seat_data, price_data = await asyncio.gather(
            fetch_json_safe(session, seat_url),
            fetch_json_safe(session, price_url)
        )
        if not seat_data or not price_data:
            return None
        seats = seat_data.get("seats", [])
        sold = sum(1 for s in seats if s.get("status") == "Sold")
        available = sum(1 for s in seats if s.get("status") == "Available")
        total = sold + available
        blocked = 0
        ticket_types = price_data.get("ticketTypes", [])
        if ticket_types:
            adult = next(
                (x for x in ticket_types if "adult" in x.get("displayName", "").lower()),
                ticket_types[0]
            )
            price = adult.get("price", 0) / 100
        else:
            price = 0
        gross = round(sold * price, 2)
        return {
            "venue": theatre_name,
            "movie": film_name,
            "perfIx": performance_id,
            "date": SCH_DATE,
            "time": showtime,
            "total": total,
            "available": available,
            "blocked": blocked,
            "sold": sold,
            "gross": gross,
            "gross_with_tax": gross,
            "per_ticket": {
                "net": price,
                "tax": 0,
                "fee": 0,
                "grand": price
            }
        }

    for theatre in schedule.get("schedules", []):
        theatre_id = theatre.get("theatre_code")
        theatre_name = theatre.get("theatre_name")
        for day in theatre.get("schedule_days", []):
            if day.get("schedule_date") != SCH_DATE:
                continue
            for film in day.get("films", []):
                film_name = film.get("titlename")
                for perf in film.get("performances", []):
                    performance_id = perf.get("performanceid")
                    showtime = perf.get("showtime")
                    tasks.append(
                        process_perf(
                            theatre_name,
                            theatre_id,
                            film_name,
                            showtime,
                            performance_id
                        )
                    )
    results_raw = await asyncio.gather(*tasks)
    for r in results_raw:
        if r:
            results.append(r)
    print("[GOLDENEYE] shows:", len(results))
    return results

# ---------------- CINEPLEX – DYNAMIC HINDI MOVIES ---------------- #

async def scrape_cineplex():
    print("\n[CINEPLEX] superfast scan starting")

    # 1) Fetch all movies and filter for "Hindi"
    movies_url = (
        "https://apis.cineplex.com/prod/cpx/theatrical/api/v1/movies"
        "?language=en&skip=0&take=1000&filterEvents=false"
        "&removeIrrelevantFilms=true&onePosterExcluded=true"
    )

    connector = aiohttp.TCPConnector(limit=800)
    async with aiohttp.ClientSession(headers=CINE_HEADERS, connector=connector) as session:
        movie_data = await fetch_json(session, movies_url)
        if not movie_data or "items" not in movie_data:
            print("[CINEPLEX] Failed to fetch movies or invalid response")
            return []

        hindi_movies = {}
        for m in movie_data["items"]:
            name = m.get("name", "")
            if "hindi" in name.lower():
                film_id = m["id"]
                hindi_movies[film_id] = {"atp": 15, "name": name}

        if not hindi_movies:
            print("[CINEPLEX] No Hindi movies found – skipping Cineplex scan")
            return []

        print(f"[CINEPLEX] Found {len(hindi_movies)} Hindi movie(s)")

        # 2) Gather showtime sessions for each theatre and each Hindi movie
        results = []
        sessions = []

        async def theatre_fetch(theatre):
            tid = theatre["theatreId"]
            name = theatre.get("theatreName", "Unknown")
            local = []
            for film_id, meta in hindi_movies.items():
                url = f"https://apis.cineplex.com/prod/cpx/theatrical/api/v1/showtimes?language=en&locationId={tid}&date={CINEPLEX_DATE}&filmId={film_id}"
                data = await fetch_json(session, url)
                if not isinstance(data, list):
                    continue
                for t in data:
                    if not t.get("dates"):
                        continue
                    for m in t["dates"][0].get("movies", []):
                        for e in m.get("experiences", []):
                            for s in e.get("sessions", []):
                                sid = s.get("vistaSessionId")
                                if not sid:
                                    continue
                                time = s.get("showtime") or s.get("startTime") or "00:00"
                                local.append({
                                    "tid": tid,
                                    "venue": name,
                                    "sid": sid,
                                    "time": time,
                                    "movie": meta["name"],
                                    "atp": meta["atp"]
                                })
            return local

        theatre_tasks = [theatre_fetch(t) for t in CINEPLEX_THEATRES]
        theatre_results = await asyncio.gather(*theatre_tasks)
        for r in theatre_results:
            sessions.extend(r)

        # Deduplicate sessions (same theatre + session id)
        unique = {}
        for s in sessions:
            key = (s["tid"], s["sid"])
            unique[key] = s
        sessions = list(unique.values())
        print("[CINEPLEX] sessions (deduped):", len(sessions))

        # 3) Fetch seat availability for each session
        async def seat_scan(s):
            url = f"https://apis.cineplex.com/prod/ticketing/api/v1/theatre/{s['tid']}/showtime/{s['sid']}/seat-availability"
            data = await fetch_json(session, url)
            if not data:
                return None
            sold = 0
            avail = 0
            for v in data.get("seatAvailabilities", {}).values():
                if v == "Occupied":
                    sold += 1
                elif v == "Available":
                    avail += 1
            total = sold + avail
            gross = round(sold * s["atp"], 2)
            return {
                "venue": s["venue"],
                "movie": s["movie"],
                "perfIx": s["sid"],
                "date": SCH_DATE,
                "time": s["time"],
                "total": total,
                "available": avail,
                "blocked": 0,
                "sold": sold,
                "gross": gross,
                "gross_with_tax": gross,
                "per_ticket": {
                    "net": s["atp"],
                    "tax": 0,
                    "fee": 0,
                    "grand": s["atp"]
                }
            }

        tasks = [seat_scan(s) for s in sessions]
        seat_results = await asyncio.gather(*tasks)
        for r in seat_results:
            if r:
                results.append(r)

    print("[CINEPLEX] shows:", len(results))
    return results

# ---------------- SAVE ---------------- #

def save_results(flat_list):
    out_dir="Canada Data"
    os.makedirs(out_dir,exist_ok=True)
    out_file=os.path.join(out_dir,f"{SCH_DATE}_json.json")
    log_file=os.path.join(out_dir,f"{SCH_DATE}_logs.json")

    if os.path.exists(out_file):
        with open(out_file) as f:
            old=json.load(f)
    else:
        old=[]

    index={(d["venue"],d["movie"],d["perfIx"],d["date"],d["time"]):d for d in old}
    for d in flat_list:
        key=(d["venue"],d["movie"],d["perfIx"],d["date"],d["time"])
        index[key]=d
    merged=list(index.values())

    with open(out_file,"w") as f:
        json.dump(merged,f,indent=2)

    total_gross=sum(x["gross_with_tax"] for x in merged)
    sold=sum(x["sold"] for x in merged)
    capacity=sum(x["total"] for x in merged)

    log={
        "time":datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %I:%M:%S %p"),
        "total_gross_usd":round(total_gross,2),
        "total_shows":len(merged),
        "avg_occupancy":round((sold/capacity)*100 if capacity else 0,2),
        "tickets_sold":sold,
        "unique_venues":len(set(x["venue"] for x in merged))
    }

    if os.path.exists(log_file):
        with open(log_file) as f:
            logs=json.load(f)
    else:
        logs=[]
    logs.append(log)
    with open(log_file,"w") as f:
        json.dump(logs,f,indent=2)

    print("\nSaved:",out_file)
    print("Log updated:",log_file)

# ---------------- MAIN ---------------- #

async def main():
    connector=aiohttp.TCPConnector(limit=300)
    async with aiohttp.ClientSession(headers=HEADERS,connector=connector) as session:
        omni=await scrape_omni(session)
        york=await scrape_york(session)
        cine=await scrape_cineplex()
        flat_list=omni+york+cine
        print("\nTotal Shows:",len(flat_list))
        save_results(flat_list)

asyncio.run(main())
