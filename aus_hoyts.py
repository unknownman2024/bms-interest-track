import asyncio
import aiohttp
import random
import os
import json
from collections import defaultdict
from tqdm.asyncio import tqdm_asyncio
from tabulate import tabulate
from brotli import decompress   # test import

# ---------------- CONFIG ----------------
ALL_MOVIES = False  # set True to fetch all movies
TARGET_MOVIE_IDS = ["HO00010652", "HO00010666", "HO00010700"]
CONCURRENCY_LIMIT = 10  # max concurrent requests

CINEMAS_URL = "https://hoytsaus.freemail20251808.workers.dev/?api=cinemaapi/api/cinemas"
MOVIES_URL = "https://hoytsaus.freemail20251808.workers.dev/?api=cinemaapi/api/movies/"
SESSIONS_URL_TEMPLATE = "https://hoytsaus.freemail20251808.workers.dev/?api=cinemaapi/api/sessions/{cinema_id}"
SEATS_URL_TEMPLATE = "https://hoytsaus.freemail20251808.workers.dev/?api=ticketing/api/v1/ticket/seats/{cinema_id}/{session_id}"
TICKET_URL_TEMPLATE = "https://hoytsaus.freemail20251808.workers.dev/?api=ticketing/api/v1/ticket/{cinema_id}/{session_id}"



# Example User-Agent pool
USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{version}) Gecko/20100101 Firefox/{version}",
    # Chrome on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_{minor}_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36",
    # Safari on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_{minor}_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/{safari_ver} Safari/605.1.15",
]

def get_random_user_agent():
    template = random.choice(USER_AGENTS)
    return template.format(
        version=f"{random.randint(70,120)}.0.{random.randint(1000,5000)}.{random.randint(0,150)}",
        minor=random.randint(12, 15),
        safari_ver=f"{random.randint(13,17)}.0.{random.randint(1,3)}",
    )

def get_random_ip():
    return ".".join(str(random.randint(1, 255)) for _ in range(4))

def get_seatmap_headers():
    random_ip = get_random_ip()
    return {
        "User-Agent": get_random_user_agent(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.hoyts.com.au/",
        "Origin": "https://www.hoyts.com.au",
        "Connection": "keep-alive",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "DNT": "1",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        "Accept-Encoding": "gzip, deflate, br, zstd",
    }


HEADERS = get_seatmap_headers()

# ---------------- HELPERS ----------------
async def fetch_json(session, url):
    try:
        async with session.get(url, headers=HEADERS) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                text = await resp.text()
                print(f"⚠️ HTTP {resp.status} for {url}\nHeaders: {resp.headers}\nBody: {text[:300]}")
                return {"__error__": f"HTTP {resp.status}"}
    except Exception as e:
        return {"__error__": str(e)}

async def fetch_cinemas(session):
    return await fetch_json(session, CINEMAS_URL)

async def fetch_movies(session):
    data = await fetch_json(session, MOVIES_URL) or []
    if isinstance(data, dict) and "__error__" in data:
        print(f"⚠️ Movies fetch error: {data['__error__']}")
        return {}
    movies_map = {}
    for m in data:
        movies_map[m["vistaId"]] = {
            "name": m.get("name"),
            "summary": m.get("summary"),
            "duration": m.get("duration"),
            "releaseDate": m.get("releaseDate"),
            "posterImage": m.get("posterImage") if "posterImage" in m else None
        }
    return movies_map

async def fetch_sessions(session, cinema_id):
    url = SESSIONS_URL_TEMPLATE.format(cinema_id=cinema_id)
    sessions = await fetch_json(session, url) or []
    if isinstance(sessions, dict) and "__error__" in sessions:
        return []
    if not ALL_MOVIES:
        sessions = [s for s in sessions if s.get("movieId") in TARGET_MOVIE_IDS]
    return sessions

async def fetch_adult_price(session, cinema_id, session_id, sem):
    async with sem:  # concurrency limit
        url = TICKET_URL_TEMPLATE.format(cinema_id=cinema_id, session_id=session_id)
        data = await fetch_json(session, url)
        if isinstance(data, dict) and "__error__" in data:
            raise Exception(data["__error__"])
        price = 27.0
        if data and "ticketTypes" in data:
            tickets = data["ticketTypes"]
            adult = next((t for t in tickets if "adult" in t["name"].lower() and t["priceInCents"] > 0), None)
            if not adult:
                adult = next((t for t in tickets if t["name"].strip().lower() == "stnd adult" and t["priceInCents"] > 0), None)
            if adult:
                price = adult["priceInCents"] / 100
        return price


async def fetch_seat_stats(session, cinema_id, sess, price, sem):
    async with sem:  # concurrency limit
        url = SEATS_URL_TEMPLATE.format(cinema_id=cinema_id, session_id=sess["id"])
        data = await fetch_json(session, url) or {}
        if isinstance(data, dict) and "__error__" in data:
            raise Exception(data["__error__"])
        total = sold = 0
        for row in data.get("rows", []):
            for seat in row.get("seats", []):
                total += 1
                if seat.get("sold"):
                    sold += 1
        available = total - sold
        total_gross = sold * price
        max_gross = total * price
        occupancy = (sold / total * 100) if total else 0
        return {
            "total": total,
            "sold": sold,
            "available": available,
            "occupancy": round(occupancy, 2),
            "max_gross": max_gross,
            "total_gross": total_gross,
            "price": price,
        }

async def process_session(session, cinema_id, sess, sem):
    try:
        price = await fetch_adult_price(session, cinema_id, sess["id"], sem)
        stats = await fetch_seat_stats(session, cinema_id, sess, price, sem)
        simplified = {
            "id": sess["id"],
            "cinemaId": cinema_id,
            "movieId": sess["movieId"],
            "date": sess.get("showDate") or sess.get("date"),
            "typeId": sess.get("typeId"),
            "screenName": sess.get("screenName"),
            "operator": sess.get("operator"),
            "total": stats["total"],
            "sold": stats["sold"],
            "available": stats["available"],
            "occupancy": stats["occupancy"],
            "max_gross": stats["max_gross"],
            "total_gross": stats["total_gross"],
            "price": stats["price"],
            "error": False,
        }
        return simplified
    except Exception as e:
        print(f"⚠️ Error in process_session {sess['id']}: {e}")
        return {
            "id": sess["id"],
            "cinemaId": cinema_id,
            "movieId": sess.get("movieId"),
            "date": sess.get("showDate") or sess.get("date"),
            "screenName": sess.get("screenName"),
            "operator": sess.get("operator"),
            "error": True,
            "error_msg": str(e),
        }

# ---------------- FILE HANDLING ----------------
def load_existing_data(filepath):
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            return {str(item["id"]): item for item in json.load(f)}
    return {}

def save_data(filepath, data_dict):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(list(data_dict.values()), f, indent=2)

# ---------------- MAIN ----------------
async def main():
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)   # yaha banaya
    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=connector) as session:
        cinemas = await fetch_cinemas(session)
        movies_map = await fetch_movies(session)

        cinema_ids = [c["id"] for c in cinemas if isinstance(c, dict) and "id" in c]
        print(f"🎬 Total cinemas found: {len(cinema_ids)}")

        # fetch sessions
        all_sessions = []
        for cinema_id in cinema_ids:
            sessions = await fetch_sessions(session, cinema_id)
            for s in sessions:
                s["cinemaId"] = cinema_id
            all_sessions.extend(sessions)
        print(f"📊 Total sessions fetched: {len(all_sessions)}")

        # process sessions
        tasks = [process_session(session, s["cinemaId"], s, sem) for s in all_sessions]
        all_results = []
        for f in tqdm_asyncio.as_completed(tasks, total=len(tasks), desc="Fetching seats & prices"):
            try:
                res = await f
                if res:
                    all_results.append(res)
            except Exception as e:
                print(f"⚠️ Session failed: {e}")

    # group by date
    sessions_by_date = defaultdict(dict)
    for r in all_results:
        show_date = r.get("date")
        show_date = show_date.split("T")[0] if show_date else "unknown"
        sessions_by_date[show_date][str(r["id"])] = r

    for show_date, data_dict in sessions_by_date.items():
        diff_path = f"Australia Data/{show_date}-data.json"
        existing_data = load_existing_data(diff_path)

        # merge
        for sid, record in data_dict.items():
            if record.get("error"):
                if sid not in existing_data:
                    existing_data[sid] = record
                else:
                    existing_data[sid]["last_error"] = record.get("error_msg")
            else:
                existing_data[sid] = record

        save_data(diff_path, existing_data)
        print(f"✅ Diff saved to {diff_path}")

        # build summary (skip error sessions)
        summary = {}
        for r in existing_data.values():
            if r.get("error"):
                continue
            movie = r["movieId"]
            if movie not in summary:
                summary[movie] = {
                    "sessions": 0,
                    "venues": set(),
                    "sold": 0,
                    "available": 0,
                    "total": 0,
                    "gross": 0,
                    "max_gross": 0,
                }
            agg = summary[movie]
            agg["sessions"] += 1
            agg["venues"].add(r["cinemaId"])
            agg["sold"] += r["sold"]
            agg["available"] += r["available"]
            agg["total"] += r["total"]
            agg["gross"] += r["total_gross"]
            agg["max_gross"] += r["max_gross"]

        summary_list = []
        for movie, agg in summary.items():
            agg["venues"] = len(agg["venues"])
            agg["occupancy"] = round((agg["sold"] / agg["total"] * 100) if agg["total"] else 0, 2)

            # 🔥 add metadata
            if movie in movies_map:
                agg.update(movies_map[movie])

            agg["id"] = movie
            summary_list.append(agg)

        summary_path = f"Australia Data/{show_date}-summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary_list, f, indent=2)
        print(f"📊 Summary saved to {summary_path}")

        # ---- UPDATE MOVIE DATES ----
        movie_dates_path = "Australia Data/movie_dates.json"

        # load existing
        if os.path.exists(movie_dates_path):
            with open(movie_dates_path, "r") as f:
                movie_dates = json.load(f)
        else:
            movie_dates = {}

        for agg in summary_list:
            mid = agg["id"]

            if mid not in movie_dates:
                movie_dates[mid] = {
                    "name": agg.get("name", "-"),
                    "poster": agg.get("posterImage"),
                    "releaseDate": agg.get("releaseDate"),   # 🔥 Add release date here
                    "dates": []
                }
            else:
                # agar pehle se hai toh bhi releaseDate update kar do (null avoid karne ke liye)
                if not movie_dates[mid].get("releaseDate"):
                    movie_dates[mid]["releaseDate"] = agg.get("releaseDate")

            if show_date not in movie_dates[mid]["dates"]:
                movie_dates[mid]["dates"].append(show_date)

        # save back
        with open(movie_dates_path, "w") as f:
            json.dump(movie_dates, f, indent=2)
        print(f"📅 Movie dates updated -> {movie_dates_path}")


        # pretty print
        print(f"\n📅 Date: {show_date}")
        table = []
        headers = ["MovieID", "Sessions", "Venues", "Sold", "Available", "Total Seats", "Occ%", "Gross", "MaxGross", "Name"]
        for agg in summary_list:
            table.append([
                agg["id"],
                agg["sessions"],
                agg["venues"],
                agg["sold"],
                agg["available"],
                agg["total"],
                agg["occupancy"],
                round(agg["gross"], 2),
                round(agg["max_gross"], 2),
                agg.get("name", "-")
            ])
        print(tabulate(table, headers=headers, tablefmt="fancy_grid"))

if __name__ == "__main__":
    asyncio.run(main())
