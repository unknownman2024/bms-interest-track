import asyncio
import aiohttp
import json
import html
import os
import re
import csv
import time
import random
import threading
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
#  DATE LOGIC
# ============================================================

SCH_DATE = datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")

print("Tracking date:", SCH_DATE)

# ============================================================
#  CONFIG
# ============================================================

OMNI_VENUES_NOTNEEDED = []
OMNI_VENUES = [
    "https://omniwebticketing5.com/orleans/",
]
YORK_THEATRES = list(range(1, 8))

# ---------- Convert to Cineplex format ---------- #
cine_date = datetime.fromisoformat(SCH_DATE)
CINEPLEX_DATE = f"{cine_date.month}%2F{cine_date.day}%2F{cine_date.year}"
print("Cineplex date:", CINEPLEX_DATE)

# (The json file for CINEPLEX_THEATRES is still needed)
with open("cineplexcanada.json") as f:
    CINEPLEX_THEATRES = json.load(f)["nearbyTheatres"]

HEADERS = {
    "accept": "*/*",
    "origin": "https://goldeneyecinemas.com",
    "referer": "https://goldeneyecinemas.com/",
    "user-agent": "Mozilla/5.0",
    "x-api-key": "FORYyLdL47yr:)QuAVytMvaYdfZIcYecwX"
}

CINE_HEADERS = {
    "accept": "*/*",
    "ocp-apim-subscription-key": "dcdac5601d864addbc2675a2e96cb1f8",
    "origin": "https://www.cineplex.com",
    "referer": "https://www.cineplex.com/",
    "User-Agent": "Mozilla/5.0"
}

# ============================================================
#  HTTP (async helpers)
# ============================================================

async def fetch_json(session, url):
    for i in range(3):
        try:
            async with session.get(url) as r:
                return await r.json()
        except:
            await asyncio.sleep(2 ** i)
    return None

async def fetch(session, url):
    for i in range(3):
        try:
            async with session.get(url) as r:
                return await r.text()
        except:
            await asyncio.sleep(2 ** i)
    return None

# ============================================================
#  OMNI
# ============================================================

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
        print(clean[e.pos - 200:e.pos + 200])
        return {}

async def scrape_omni(session):
    print("\n[OMNI] scanning venues")
    results = []
    for base in OMNI_VENUES:
        url = f"{base}?schdate={SCH_DATE}"
        html_page = await fetch(session, url)
        if not html_page:
            continue
        movie_data = extract_gmoviedata(html_page)
        venue_name = base.rstrip("/").split("/")[-1]
        for movie in movie_data.values():
            title = movie["title"].strip()
            for aud in movie["schAuds"].values():
                for perf in aud["schPerfsReserved"].values():
                    results.append({
                        "venue": venue_name,
                        "movie": title,
                        "perfIx": perf["perfIx"],
                        "date": perf["schDateStr"],
                        "time": perf["startTimeStr"],
                        "total": 0,
                        "available": 0,
                        "blocked": 0,
                        "sold": 0,
                        "gross": 0,
                        "gross_with_tax": 0,
                        "per_ticket": {"net": 0, "tax": 0, "fee": 0, "grand": 0}
                    })
    print("[OMNI] shows:", len(results))
    return results

# ============================================================
#  GOLDENEYE (REPLACEMENT FOR YORK)
# ============================================================

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
            await asyncio.sleep(2 ** i)
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

# ============================================================
#  CINEPLEX – DYNAMIC HINDI MOVIES
# ============================================================

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
                                time_val = s.get("showtime") or s.get("startTime") or "00:00"
                                local.append({
                                    "tid": tid,
                                    "venue": name,
                                    "sid": sid,
                                    "time": time_val,
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

# ================================================================
#  LANDMARK CINEMAS – HINDI/TELUGU/TAMIL/MALAYALAM/KANNADA SCRAPER
#  (Merged into Canada aggregator; outputs same dict shape)
# ================================================================

# ---------- CONFIGURATION ----------
LANDMARK_KEYWORDS = ["hindi", "telugu", "tamil", "malayalam", "kannada"]
LANDMARK_PRICE_MAP = {'2D': 14, 'IMAX': 18}
LANDMARK_CONCURRENCY = 1                 # seatmap workers
LANDMARK_MOVIES_CONCURRENCY = 1          # showtime workers
LANDMARK_REQUEST_TIMEOUT = 20
LANDMARK_MAX_RETRIES = 3                 # per request (movies + seatmap)
LANDMARK_RETRY_BACKOFF_BASE = 2.0        # seconds -> 2, 4, 8 ...
LANDMARK_SEATMAP_MIN_DELAY = 1.0         # seconds between seatmap requests
LANDMARK_SEATMAP_MAX_DELAY = 2.0
LANDMARK_LOG_FILE = 'logs.txt'
LANDMARK_DUMP_DIR = 'seatmapdump'

# ---------- COOKIES + XSRF (fixed for every request) ----------
LANDMARK_COOKIE_STRING = (
    '_ga=GA1.1.1510103123.1787225372; '
    '__qca=P1-dc8dc41f-3a1f-46f1-9e5e-9eaa756d3bf9; '
    '_aimtellSubscriberID=3ea26818-1336-02e8-e817-b22389992090; '
    'sbt_i=WY3NGI3NDg1MjsyMDM1ODgwNTk5NjQwNDg7MzA1YmQ4OTEtNTFiOS00ZDg0LThkZjgtZTBjZjE1Yjc3ODQ4OzZGJjMzUzOGUtYzI2NC00Y2EwLTk1MDItZTg2NTczMmVjMzQ4O2Q5M2QzMzdkLTg4NjUtNGExMi04OWVhLTU4NA=; '
    '.AspNet.Cookies=NYStMx7iCTuOouFVDxXoGRAckelkIx0Jt5UgonRtqtM_0lJIkMGuRsMr0WdHnPJiw-7bEtOsWZc7drtFhOvWw0Ph5xdZLgPPwXq6PvWzLF8QB_gJJD0SQbsxZVoEqqPfpxt42jSovV8I-QYlwi4FQRh_pNzvchELyhWAvzg3dKBzZ7jqM1r3MQOugUppFDkxW4hZ5-77AAJSek8hbjHKPuy4FvtDd1A5-YhAObe53BcJTAY6OVlWoNrspnfxe3IBOClFOKzzq0BwJVPWsQdimDgUtKn2_DSYWH8fQ4thhzn4L-5cehDjCwcakGUDwiv7f0sJPWah8BYfeGavjqydTpFfI5lwz_mFFqsCDkSHnfzdNocqJ2Uripw6g6XEcKOLl19WFp93SayPs_va-sVr8ipMr2VBTUXK99heCUzB15RThwxf0wJrH3ClJ5djrkIEaQHbSAzT0zgUu3bCb4kQLz0ALmfUYZFsBYQbgtXvF7sXgGcbd8qTbiWvb3N4pewgu4IBhpHRrwsM_ReQ8bq-C300XngP2h7UJ3LC1qFGCFqcrqCV8y7NHBpVI8AKpnJaKrw0poczKpWryr1a6wJ6--2vqY-NeB48x72gkS_BBFvv8Gfzj-ChAzyqaaRY9ogtjH451BwbbRcFIu5RLvslwviA3RK_CVoWJ7l93AOWFSWq2gPxOVm_DuNO7-btDaeEB0LPx9t_m5IlJOYQiJDHufxLioERnv5AocE6HpEwIypOgUIqcmmG2tpN4kLG_CVAvioEyz3lsj1s5b_G0KrY9MGFzH0gGZ-MlSb1rD1ZmxaNtjX1_0ousSYuaHqAUnPEF4IGzG2rIZlykbRphpu5RDOxASshCALA5zWh2gTWOonzzfJDKTi3plE4KtJCCFgYsFVgPQP8K57Q-G4H0n4F6TdSvhN3JGlfgg22sjOMDmW2JAgpYXIKbg6o8u9WJ5PXaqu5LQQV2ny-FHMSvhxfuAhytcEiFPhcpcinLT1DDon5SeS1euw1h9AJxtXRDBwg1Kh2MkBLrvOMLOKSTzeBDGgxqfkJYivDE4HFyY1ErciHDNGa2fvOrJiyPd-zp0xVJICuP4zdjJ56G5UXWig06hnziIAUnP5AwBKihfX290QrVa4ZG_W92kZ_6zCRXt6_4LOKGuqQ0aj6Vhn3BbiVG9Qxy3O6lcDOMRSts9NoBQ87Izk3Y4Z_6vZfsT5sou1OQdqHbRvBc-eGbITqYgoeb4PaKem6nICqDxlJNWCdenJ5DKOOEhrLu9Tbv3BYT6KQn36mH3QwRe7ndRj26iVehjkDxkbTSsZrL4P3DlNNcUofnTE_MVzVH8D81M10XnCKOkyyaRqetqqY0Rn1YgdrPiBIfQYJjT4XCtvIDZEO3h5W8_FqX6VUpRAgZXunwJTWFSm3vMwo5FdnV29XJORrxHwLcByaU0R2va4b2rgExAl-agu1OGrPoUGgyQb64AKmzpnLVaUI2_tT87HoWaQy-eur-9iL_pz9UNTpqYOnj79aCi4nEs9i1XBrAF7CvfJ8JBKkWY4xTRqUUjeh6txT71zWn0FGeTKkxALMh9voNyW8iVqaC7OpoKIvB-aB_gFSM9v9hkffKTWqAK7VxNsV_FUXP7gIa2WVi9BiAH83btDivzFNLebFgSrgjF5UEZWjq1iMrv28-JyQQsJhdsVyYMBzxkoOaNIVhcJfjdnUkug4XbBe2-DvMzYTBiPVK7R-hM99-vBP2tIBSSmN8oqqeg7guGacJ65gniBQ8lVmN2g12A3Zy8kDoARAfrhV62wpYnBOdmVQHN_uRN7JxqhspgTRX8_N6wE_qvM6-l5pL29dU3UkBnaky2c1n-UyQvrg_-hcbBYcipxmgVrCH9chN6AY6Quv2gNVNkxB4jVMSnFKZTAQyUnNA9fMOfzzES8V7y1pSfyGCM-gj54n_iT-Nd3K7beY1VWv3AQ9bWotsQuEd7N7iUTwWhrk3fWhOdXqV9nM1wruG7OnQighv7T-Dz16ER7p3P482uLyGBB15l1gVTJ6k13AVkyD9i0eOS3AbZpjw_xsPz3KB6Pbk2DWF-pgU48y9JRL46d45uuyMQcFdYHMCTMkH1JRaV1VL8x9TcrXLbPPlWyyFFj4fkGAWMoIk6EfzzIBFNwrkqG9w7jZt62L7jrYDqx_248xaJc2NbNEsgRtsL6YcbB7um-8a2b8FuwCF0OaFxJc-iIkGiHNsZxYcOC4quWe_OjY_mcuBa_CLpwq3tBKMTbQKB8GaPmUD-WszBINzq6tLx9FWVvF901DwfmfjtsZUcv-5Kulo10CKxdK9LmtP5zK4SfAgByjO1nbip6822ubVmz5MH55E0OybP7P9ANaidwUY5nsfcRwUv87YQsqmo9vZDHqVk-vtzRRYQ5fEQUrQDn9D9y9N7vJd6mull9LK7POOH9zldqyytDGY9nmeE9BC_prCnu9fpy8QSqQMl7w_B-bk2_2T2tN9ExrxoGKeevBvwqbRlABk8xSiY5nrVeR0jij1hOewEnsmZKSz-S0ykdc8urz_Bi3i0bwOM3xF_dHOPTBhVxLyx9m0j_nM4WHfuGEO3DDb7TaeWCg8Sd56zoqAOHrSHK1P1HkASK7adFXENouT0-twCUsXC_lvWNNFlzNC7Y4RA9l2XwZKc2dYpf9K37nXpZhX9kEvXAtEMaxuMcSvvNqD4ayEYE6kEVBHoPyu_GYQ9sSE1hcDYNYu5KDMZUJ1rhnXmK7_4BGgjS-cAbpYjyzmINIMTNUTuby31QJiZP9PRd4wPBj5nEaLEL_Hno; '
    '_aimtellPromptApproved=true; '
    '_aimtellSubscriptionURL=https://www.landmarkcinemas.com/; '
    'LMC_TheatreId=7782; '
    'LMC_TheatreURL=%2Fshowtimes%2Fedson; '
    'LMC_TheatreName=%2Fnow-playing%2Fedson; '
    'm_ses=20260910133101; '
    '__cmpcccx97154=aCQqWB57AB_ovWB92taaY1k1evBZDTWDS0sLNNaIxGTANVMaAyM0jDqnhaEwJhTKxqTUmMK0VpMWomBNVqsEeV5RMiySMGhJgCyWRF6XkDABRIARgA; '
    'm_cnt=3; '
    '_ga_7M6ER5F56D=GS2.1.s1789027250$o3$g1$t1789028825$j38$l0$h0; '
    '_ga_F0SPX7GNS6=GS2.1.s1789027255$o3$g1$t1789028825$j38$l0$h0'
)

LANDMARK_XSRF_TOKEN = (
    'o_CBOYZHVNJf0Xvp76dfIZg84NGBRa8Dj3MjHhWmDLaqUTf0yQjBHaT-Ae-z3-LZJ61t8oudyrVg97'
    'BotC3_ll4hcI8xWGa5TUMf7h3O5iY1:_eT_jfhctaEiByvipV6AGIKSPNhttuLMpFPMCNwYnVnw4uE'
    'NxzDDkBPPm2A_p2EbvDT3Gw3AAc22kHny3EcdWQ8vKn-AuI3Co85eVxqhHl8ObehY4TLIsIpEyW-mkwF'
    'mPrs9DLjyk0eicn0-jTmg4Q2'
)

# ---------- RANDOM ROTATING HEADER PROFILES ----------
LANDMARK_HEADER_PROFILES = [
    {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
        'sec-ch-ua-platform': '"Windows"',
        'accept-language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
    },
    {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Google Chrome";v="152", "Chromium";v="152", "Not?A_Brand";v="24"',
        'sec-ch-ua-platform': '"Windows"',
        'accept-language': 'en-CA,en;q=0.9,en-US;q=0.8,fr-CA;q=0.6',
    },
    {
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
        'sec-ch-ua-platform': '"macOS"',
        'accept-language': 'en-US,en;q=0.9',
    },
    {
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
        'sec-ch-ua-platform': '"Linux"',
        'accept-language': 'en-GB,en;q=0.9,en-US;q=0.8',
    },
    {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',
        'sec-ch-ua': '"Firefox";v="127", "Not)A;Brand";v="99"',
        'sec-ch-ua-platform': '"Windows"',
        'accept-language': 'en-CA,en;q=0.5',
    },
    {
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0',
        'sec-ch-ua': '"Firefox";v="127", "Not)A;Brand";v="99"',
        'sec-ch-ua-platform': '"Linux"',
        'accept-language': 'en-US,en;q=0.5',
    },
    {
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15',
        'sec-ch-ua': '"Safari";v="17", "Not?A_Brand";v="24"',
        'sec-ch-ua-platform': '"macOS"',
        'accept-language': 'en-CA,en;q=0.9',
    },
]

# ---------- THEATRE MAP ----------
LANDMARK_THEATRES = {
    177: 'Campbell River', 180: 'Caledon, Bolton', 181: 'Brandon',
    182: 'Edmonton City Centre', 184: 'Calgary Country Hills',
    186: 'Winnipeg, Grant Park', 187: 'Surrey, Guildford',
    188: 'Hamilton, Jackson Square', 189: 'Kanata', 190: 'Kingston',
    191: 'Kitchener', 192: 'London', 193: 'Orleans',
    194: 'St. Catharines, Pen Centre', 195: 'Penticton',
    196: 'Calgary Shawnessy', 197: 'Spruce Grove', 200: 'Waterloo',
    201: 'Whitby', 202: 'Winkler', 203: 'Courtenay', 204: 'Cranbrook',
    206: 'Drayton Valley', 207: 'West Kelowna, Xtreme', 209: 'Fort St. John',
    211: 'Kelowna, Grand 10', 213: 'Nanaimo', 214: 'New Westminster',
    217: 'Sylvan Lake', 220: 'Port Alberni', 7779: 'Brooks',
    7782: 'Edson', 7784: 'West Kelowna, Encore', 7795: 'St. Albert',
    7796: 'Regina', 7798: 'Saskatoon', 7799: 'Fort McMurray Eagle Ridge',
    7800: 'Calgary Market Mall', 7801: 'Edmonton Tamarack', 7802: 'Windsor',
}

LANDMARK_BASE_URL = 'https://www.landmarkcinemas.com'
LANDMARK_MOVIES_BY_CINEMA_API = f'{LANDMARK_BASE_URL}/Umbraco/Api/MovieApi/MoviesByCinema'
LANDMARK_SEATMAP_API = f'{LANDMARK_BASE_URL}/Umbraco/Api/SeatMapApi/GetSessionSeatMap'

_landmark_log_lock = threading.Lock()
_landmark_rate_lock = threading.Lock()
_landmark_last_seatmap_ts = [0.0]

LANDMARK_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

# ---------- HELPERS ----------

def _lm_slugify(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

def _lm_parse_cookies(cookie_str: str) -> dict:
    out = {}
    for part in cookie_str.split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        k, v = part.split('=', 1)
        out[k.strip()] = v.strip()
    return out

def _lm_build_cookie_header(cookie_dict: dict) -> str:
    return '; '.join(f'{k}={v}' for k, v in cookie_dict.items())

def _lm_make_cookie_header_for_theatre(cinema_id: int, theatre_name: str) -> str:
    jar = _lm_parse_cookies(LANDMARK_COOKIE_STRING)
    slug = _lm_slugify(theatre_name)
    jar['LMC_TheatreId']   = str(cinema_id)
    jar['LMC_TheatreURL']  = f'%2Fshowtimes%2F{slug}'
    jar['LMC_TheatreName'] = f'%2Fnow-playing%2F{slug}'
    return _lm_build_cookie_header(jar)

# ---------- DUMP HELPERS ----------

def _lm_dump_seatmap_file(session_id, cinema_id, theatre_name, status,
                          request_payload, response_body, resp_headers,
                          attempt=1):
    os.makedirs(LANDMARK_DUMP_DIR, exist_ok=True)
    suffix = '' if attempt == 1 else f'_attempt{attempt}'
    path = os.path.join(LANDMARK_DUMP_DIR, f'{session_id}{suffix}.json')

    try:
        parsed_body = json.loads(response_body) if response_body else None
    except Exception:
        parsed_body = None

    record = {
        'sessionId': str(session_id),
        'cinemaId': int(cinema_id),
        'theatreName': theatre_name,
        'status': status,
        'attempt': attempt,
        'requestPayload': request_payload,
        'responseHeaders': dict(resp_headers),
        'responseBodyRaw': response_body,
        'responseBodyJson': parsed_body,
        'timestamp': time.time(),
    }

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

# ---------- SEAT MAP PARSER ----------

def _lm_parse_seatmap(data: dict) -> dict:
    out = {
        'total': 0,
        'sold': 0,
        'available': 0,
        'blocked': 0,
        'wheelchair': 0,
        'areas': [],
        'result_code': data.get('ResultCode'),
        'result_message': data.get('ResultMessage', ''),
    }

    body = data.get('Data') or {}
    areas = body.get('Area') or []

    for area in areas:
        area_stats = {
            'id': area.get('AreaId'),
            'description': area.get('AreaDescription', ''),
            'total': 0,
            'sold': 0,
            'available': 0,
            'blocked': 0,
            'wheelchair': 0,
        }
        rows = area.get('Rows') or {}
        for row_key, row in rows.items():
            seats = row.get('Seats') or {}
            for seat_key, seat in seats.items():
                status = seat.get('Status', -1)
                seat_type = seat.get('Type', 1)

                area_stats['total'] += 1
                out['total'] += 1

                if status == 1:
                    area_stats['sold'] += 1
                    out['sold'] += 1
                elif status == 0:
                    area_stats['available'] += 1
                    out['available'] += 1
                else:
                    area_stats['blocked'] += 1
                    out['blocked'] += 1

                if seat_type == 2:
                    area_stats['wheelchair'] += 1
                    out['wheelchair'] += 1

        out['areas'].append(area_stats)

    return out

# ---------- LOGGER ----------

def _lm_log_dump(section_title: str, lines):
    with _landmark_log_lock:
        with open(LANDMARK_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write('\n' + '=' * 78 + '\n')
            f.write(f'{section_title}\n')
            f.write('=' * 78 + '\n')
            for ln in lines:
                f.write(f'{ln}\n')

def _lm_log_seatmap_request(url, headers, body, cookie_header, extra=None):
    lines = [f'URL     : {url}', '', 'HEADERS :']
    for k, v in headers.items():
        if k.lower() == 'x-xsrf-token':
            lines.append(f'  {k}: {v[:50]}...  (len={len(v)})')
        elif k.lower() == 'cookie':
            lines.append(f'  {k}: {v[:120]}...  (len={len(v)})')
        else:
            lines.append(f'  {k}: {v}')
    lines.append('')
    lines.append(f'COOKIE HEADER (len={len(cookie_header)}):')
    lines.append(f'  {cookie_header[:300]}...')
    lines.append('')
    lines.append('PAYLOAD :')
    lines.append(f'  {body}')
    lines.append(f'  (length: {len(body)} bytes)')
    if extra:
        lines.append('')
        for ln in extra:
            lines.append(ln)
    _lm_log_dump('📤 SEATMAP REQUEST', lines)

def _lm_log_seatmap_response(status, reason, resp_headers, body, extra=None):
    lines = [f'STATUS  : {status} {reason}', '', 'RESPONSE HEADERS :']
    for k, v in resp_headers.items():
        lines.append(f'  {k}: {v}')
    lines.append('')
    lines.append('RESPONSE BODY (first 2000 chars):')
    snippet = body[:2000] + ('... [truncated]' if len(body) > 2000 else '')
    lines.append(snippet)
    if extra:
        lines.append('')
        for ln in extra:
            lines.append(ln)
    _lm_log_dump('📥 SEATMAP RESPONSE', lines)

def _lm_log_retry(kind, attempt, max_retries, wait, err, extra=None):
    lines = [
        f'KIND    : {kind}',
        f'ATTEMPT : {attempt}/{max_retries}',
        f'WAIT    : {wait:.2f}s',
        f'ERROR   : {err}',
    ]
    if extra:
        for ln in extra:
            lines.append(ln)
    _lm_log_dump('🔁 RETRY', lines)

# ---------- HEADERS ----------

def _lm_get_headers(accept: str = 'application/json, text/javascript, */*; q=0.01') -> dict:
    profile = random.choice(LANDMARK_HEADER_PROFILES)
    return {
        'authority': 'www.landmarkcinemas.com',
        'accept': accept,
        'accept-language': profile['accept-language'],
        'cache-control': 'no-cache',
        'pragma': 'no-cache',
        'origin': LANDMARK_BASE_URL,
        'referer': LANDMARK_BASE_URL + '/',
        'priority': 'u=1, i',
        'sec-ch-ua': profile['sec-ch-ua'],
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': profile['sec-ch-ua-platform'],
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': profile['user-agent'],
        'x-requested-with': 'XMLHttpRequest',
        # FIXED — never rotates
        'x-xsrf-token': LANDMARK_XSRF_TOKEN,
    }

# ---------- RETRY WRAPPER ----------

def _lm_request_with_retry(method, url, *, kind='request', max_retries=LANDMARK_MAX_RETRIES,
                           backoff_base=LANDMARK_RETRY_BACKOFF_BASE,
                           log_extra=None, **kwargs):
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.request(method, url, **kwargs)

            if r.status_code in LANDMARK_RETRYABLE_STATUS:
                raise requests.HTTPError(
                    f'HTTP {r.status_code} {r.reason}', response=r
                )

            r.raise_for_status()
            return r

        except (requests.RequestException, requests.HTTPError) as e:
            last_exc = e
            if attempt >= max_retries:
                break

            wait = (backoff_base ** attempt) + random.uniform(0.0, 1.0)
            print(f'   🔁 [LANDMARK] retry {attempt}/{max_retries - 1} in {wait:.1f}s — {e}')
            _lm_log_retry(kind, attempt, max_retries, wait, e, extra=log_extra)
            time.sleep(wait)

    assert last_exc is not None
    raise last_exc

# ---------- RATE LIMIT HELPER ----------

def _lm_seatmap_delay():
    target = random.uniform(LANDMARK_SEATMAP_MIN_DELAY, LANDMARK_SEATMAP_MAX_DELAY)
    with _landmark_rate_lock:
        now = time.monotonic()
        elapsed = now - _landmark_last_seatmap_ts[0]
        sleep_for = max(0.0, target - elapsed)
        _landmark_last_seatmap_ts[0] = now + sleep_for
    if sleep_for > 0:
        time.sleep(sleep_for)

# ---------- HTTP HELPERS ----------

def _lm_fetch_movies_for_cinema(cinema_id: int):
    params = {
        'cinemaId': str(cinema_id),
        'splitByAttributes': 'true',
        'expandSessions': 'true',
    }
    headers = _lm_get_headers(accept='*/*')
    headers['cookie'] = _lm_make_cookie_header_for_theatre(cinema_id, LANDMARK_THEATRES[cinema_id])

    r = _lm_request_with_retry(
        'GET',
        LANDMARK_MOVIES_BY_CINEMA_API,
        kind='movies',
        params=params,
        headers=headers,
        timeout=LANDMARK_REQUEST_TIMEOUT,
        log_extra=[f'CINEMA_ID: {cinema_id}', f'THEATRE: {LANDMARK_THEATRES[cinema_id]}'],
    )
    return r.json()

def _lm_fetch_seat_map(session_id, cinema_id, theatre_name, log_context=''):
    last_exc = None

    for attempt in range(1, LANDMARK_MAX_RETRIES + 1):
        headers = _lm_get_headers()
        headers['content-type'] = 'application/json; charset=UTF-8'

        cookie_header = _lm_make_cookie_header_for_theatre(cinema_id, theatre_name)
        headers['cookie'] = cookie_header

        body = json.dumps(
            {'SessionId': str(session_id), 'CinemaId': int(cinema_id)},
            separators=(',', ':'),
        )

        _lm_log_seatmap_request(
            url=LANDMARK_SEATMAP_API, headers=headers, body=body,
            cookie_header=cookie_header,
            extra=[
                f'CONTEXT  : {log_context}',
                f'CINEMA_ID: {cinema_id}',
                f'SESSION  : {session_id}',
                f'THEATRE  : {theatre_name}',
                f'ATTEMPT  : {attempt}/{LANDMARK_MAX_RETRIES}',
            ],
        )

        try:
            r = requests.post(
                LANDMARK_SEATMAP_API,
                headers=headers,
                data=body,
                timeout=LANDMARK_REQUEST_TIMEOUT,
            )

            _lm_log_seatmap_response(
                status=r.status_code, reason=r.reason,
                resp_headers=dict(r.headers), body=r.text,
                extra=[
                    f'CONTEXT  : {log_context}',
                    f'CINEMA_ID: {cinema_id}',
                    f'SESSION  : {session_id}',
                    f'THEATRE  : {theatre_name}',
                    f'ATTEMPT  : {attempt}/{LANDMARK_MAX_RETRIES}',
                ],
            )

            _lm_dump_seatmap_file(
                session_id=session_id,
                cinema_id=cinema_id,
                theatre_name=theatre_name,
                status=r.status_code,
                request_payload={'SessionId': str(session_id),
                                 'CinemaId': int(cinema_id)},
                response_body=r.text,
                resp_headers=dict(r.headers),
                attempt=attempt,
            )

            if r.status_code in LANDMARK_RETRYABLE_STATUS:
                raise requests.HTTPError(
                    f'HTTP {r.status_code} {r.reason}', response=r
                )

            r.raise_for_status()
            return r.json()

        except (requests.RequestException, requests.HTTPError) as e:
            last_exc = e
            if attempt >= LANDMARK_MAX_RETRIES:
                break

            wait = (LANDMARK_RETRY_BACKOFF_BASE ** attempt) + random.uniform(0.0, 1.0)
            print(f'   🔁 [LANDMARK] seatmap retry {attempt}/{LANDMARK_MAX_RETRIES - 1} '
                  f'in {wait:.1f}s — {e}')
            _lm_log_retry(
                'seatmap', attempt, LANDMARK_MAX_RETRIES, wait, e,
                extra=[
                    f'CONTEXT  : {log_context}',
                    f'CINEMA_ID: {cinema_id}',
                    f'SESSION  : {session_id}',
                    f'THEATRE  : {theatre_name}',
                ],
            )
            time.sleep(wait)

    assert last_exc is not None
    raise last_exc

# ---------- CONCURRENCY POOL ----------

def _lm_async_pool(limit, items, worker):
    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=limit) as ex:
        futs = {ex.submit(worker, it): i for i, it in enumerate(items)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                results[i] = {'__error__': str(e)}
    return results

# ---------- SYNC ENTRY: scrape Landmark and return common-shape list ----------

def _landmark_main_sync():
    """
    Synchronous Landmark scrape.
    Returns a flat list of dicts in the same shape used by OMNI / GoldenEye / Cineplex:
        {venue, movie, perfIx, date, time, total, available, blocked,
         sold, gross, gross_with_tax, per_ticket}
    """
    # Fresh log per run
    with open(LANDMARK_LOG_FILE, 'w', encoding='utf-8') as f:
        f.write(f'Landmark scraper log — {time.ctime()}\n')
        f.write(f'Keywords: {LANDMARK_KEYWORDS}  Date: {SCH_DATE}\n')

    os.makedirs(LANDMARK_DUMP_DIR, exist_ok=True)

    print(f'🍁 Landmark Scraper — keywords={LANDMARK_KEYWORDS} date={SCH_DATE}')
    print(f'   📝 logs → {LANDMARK_LOG_FILE} | 📦 dumps → {LANDMARK_DUMP_DIR}/')
    print(f'   ⚙️  concurrency movies={LANDMARK_MOVIES_CONCURRENCY}  seatmap={LANDMARK_CONCURRENCY}')
    print(f'   ⚙️  retries={LANDMARK_MAX_RETRIES} (backoff base {LANDMARK_RETRY_BACKOFF_BASE}s)')
    print(f'   ⚙️  seatmap gap {LANDMARK_SEATMAP_MIN_DELAY}–{LANDMARK_SEATMAP_MAX_DELAY}s')

    theatre_ids = list(LANDMARK_THEATRES.keys())

    def fetch_theatre(cid):
        name = LANDMARK_THEATRES[cid]
        try:
            movies = _lm_fetch_movies_for_cinema(cid)
            return {'theatreId': cid, 'theatreName': name, 'movies': movies}
        except Exception as e:
            print(f'❌ [LANDMARK] Movies [{name}]: {e}')
            return {'theatreId': cid, 'theatreName': name, 'movies': []}

    print('📥 [LANDMARK] Fetching showtimes per theatre...')
    theatre_results = _lm_async_pool(LANDMARK_MOVIES_CONCURRENCY, theatre_ids, fetch_theatre)

    shows_to_fetch = []
    for tr in theatre_results:
        if not tr or '__error__' in tr:
            continue
        for film in tr['movies']:
            title = film.get('Title', '') or ''
            if not any(kw.lower() in title.lower() for kw in LANDMARK_KEYWORDS):
                continue
            sess_count = 0
            for sess in film.get('Sessions', []) or []:
                if sess.get('NewDate') != SCH_DATE:
                    continue
                for exp in sess.get('ExperienceTypes', []) or []:
                    attrs = exp.get('ExperienceAttributes', []) or []
                    lang, version = 'English', '2D'
                    for a in attrs:
                        n = (a.get('Name') or '').lower()
                        if n == 'language':
                            lang = a.get('Value', lang)
                        elif n == 'version':
                            version = a.get('Value', version)
                    for show in exp.get('Times', []) or []:
                        sid = show.get('Scheduleid')
                        if not sid:
                            continue
                        shows_to_fetch.append({
                            'theatreId': tr['theatreId'],
                            'theatreName': tr['theatreName'],
                            'movieTitle': title,
                            'filmId': film.get('FilmId'),
                            'sessionId': sid,
                            'cinemaId': int(show.get('CinemaId', tr['theatreId'])),
                            'showTime': show.get('StartTime'),
                            'screen': show.get('Screen', ''),
                            'lang': lang,
                            'version': version,
                        })
                        sess_count += 1
            if sess_count:
                print(f'🎬 [LANDMARK] [{tr["theatreName"]}] "{title}" → {sess_count} show(s) on {SCH_DATE}')

    if not shows_to_fetch:
        print('❌ [LANDMARK] No matching shows found.')
        return []

    unique_theatres = len({s['theatreId'] for s in shows_to_fetch})
    print(f'🎬 [LANDMARK] {len(shows_to_fetch)} show(s) across {unique_theatres} theatre(s). '
          f'Fetching seats (1–2s delay between requests)...\n')

    def fetch_seats(show):
        tag = (f'[LANDMARK] [{show["theatreName"]}] {show["movieTitle"]} '
               f'@{show["showTime"]} (session {show["sessionId"]})')

        _lm_seatmap_delay()

        try:
            data = _lm_fetch_seat_map(
                show['sessionId'], int(show['cinemaId']),
                show['theatreName'], log_context=tag)

            stats = _lm_parse_seatmap(data)

            if stats['result_code'] not in (0, None):
                print(f'⚠️  {tag} → ResultCode={stats["result_code"]}: {stats["result_message"]}')
                return {**show, 'sold': 0, 'total': 0, 'occupancy': 0,
                        'avgPrice': 0, 'gross': 0,
                        'wheelchair': 0, 'available': 0, 'blocked': 0,
                        'areas': [], 'result_code': stats['result_code'],
                        'result_message': stats['result_message'],
                        'error': f'ResultCode={stats["result_code"]}: '
                                 f'{stats["result_message"]}'}

            if stats['total'] == 0:
                print(f'⚠️  {tag} → empty seat map (ResultCode={stats["result_code"]})')
                return {**show, 'sold': 0, 'total': 0, 'occupancy': 0,
                        'avgPrice': 0, 'gross': 0,
                        'wheelchair': 0, 'available': 0, 'blocked': 0,
                        'areas': [], 'result_code': stats['result_code'],
                        'result_message': stats['result_message'],
                        'error': 'empty seat map'}

            sold  = stats['sold']
            total = stats['total']
            occ   = round(sold / total * 100, 2) if total else 0
            price = LANDMARK_PRICE_MAP.get(show['version'], 15)
            gross = price * sold

            area_desc = ' | '.join(
                f"{a['description']}:{a['sold']}/{a['total']}"
                for a in stats['areas']
            )

            print(f'💺 {tag} → sold={sold}/{total} ({occ}%) [wc={stats["wheelchair"]}] {area_desc}')

            return {
                **show,
                'sold': sold,
                'total': total,
                'occupancy': occ,
                'avgPrice': price,
                'gross': gross,
                'wheelchair': stats['wheelchair'],
                'available': stats['available'],
                'blocked': stats['blocked'],
                'areas': stats['areas'],
                'result_code': stats['result_code'],
                'result_message': stats['result_message'],
                'error': None,
            }
        except Exception as e:
            print(f'❌ {tag} → {e}')
            return {**show, 'sold': 0, 'total': 0, 'occupancy': 0,
                    'avgPrice': 0, 'gross': 0,
                    'wheelchair': 0, 'available': 0, 'blocked': 0,
                    'areas': [], 'result_code': None,
                    'result_message': '', 'error': str(e)}

    shows_with_seats = _lm_async_pool(LANDMARK_CONCURRENCY, shows_to_fetch, fetch_seats)
    valid_shows = [s for s in shows_with_seats if s and not s.get('error')]
    failed = [s for s in shows_with_seats if s and s.get('error')]
    print(f'ℹ️  [LANDMARK] Seat summary: {len(valid_shows)} ok / {len(failed)} failed.')

    # ---- Convert to common shape used by save_results() ----
    common = []
    for s in valid_shows:
        price = LANDMARK_PRICE_MAP.get(s['version'], 15)
        gross = round(price * s['sold'], 2)
        common.append({
            "venue": s['theatreName'],
            "movie": s['movieTitle'],
            "perfIx": s['sessionId'],
            "date": SCH_DATE,
            "time": s['showTime'],
            "total": s['total'],
            "available": s['available'],
            "blocked": s['blocked'],
            "sold": s['sold'],
            "gross": gross,
            "gross_with_tax": gross,
            "per_ticket": {"net": price, "tax": 0, "fee": 0, "grand": price}
        })
    return common

# ---------- ASYNC WRAPPER ----------

async def scrape_landmark():
    print("\n[LANDMARK] scanning theatres")
    results = await asyncio.to_thread(_landmark_main_sync)
    print("[LANDMARK] shows:", len(results))
    return results

# ============================================================
#  SAVE
# ============================================================

def save_results(flat_list):
    out_dir = "Canada Data"
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f"{SCH_DATE}_json.json")
    log_file = os.path.join(out_dir, f"{SCH_DATE}_logs.json")

    if os.path.exists(out_file):
        with open(out_file) as f:
            old = json.load(f)
    else:
        old = []

    index = {(d["venue"], d["movie"], d["perfIx"], d["date"], d["time"]): d for d in old}
    for d in flat_list:
        key = (d["venue"], d["movie"], d["perfIx"], d["date"], d["time"])
        index[key] = d
    merged = list(index.values())

    with open(out_file, "w") as f:
        json.dump(merged, f, indent=2)

    total_gross = sum(x["gross_with_tax"] for x in merged)
    sold = sum(x["sold"] for x in merged)
    capacity = sum(x["total"] for x in merged)

    log = {
        "time": datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %I:%M:%S %p"),
        "total_gross_usd": round(total_gross, 2),
        "total_shows": len(merged),
        "avg_occupancy": round((sold / capacity) * 100 if capacity else 0, 2),
        "tickets_sold": sold,
        "unique_venues": len(set(x["venue"] for x in merged))
    }

    if os.path.exists(log_file):
        with open(log_file) as f:
            logs = json.load(f)
    else:
        logs = []
    logs.append(log)
    with open(log_file, "w") as f:
        json.dump(logs, f, indent=2)

    print("\nSaved:", out_file)
    print("Log updated:", log_file)

# ============================================================
#  MAIN
# ============================================================

async def main():
    connector = aiohttp.TCPConnector(limit=300)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
        omni = await scrape_omni(session)
        york = await scrape_york(session)
        cine = await scrape_cineplex()
        landmark = await scrape_landmark()
        flat_list = omni + york + cine + landmark
        print("\nTotal Shows:", len(flat_list))
        save_results(flat_list)

asyncio.run(main())
