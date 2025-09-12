import asyncio
import cloudscraper
import random
import os
import json
from collections import defaultdict
from tqdm.asyncio import tqdm_asyncio
from tabulate import tabulate

# ---------------- CONFIG ----------------
ALL_MOVIES = False
TARGET_MOVIE_IDS = ["HO00010652"]
CONCURRENCY_LIMIT = 50

CINEMAS_URL = "https://apim.hoyts.com.au/au/cinemaapi/api/cinemas"
MOVIES_URL = "https://apim.hoyts.com.au/au/cinemaapi/api/movies/"
SESSIONS_URL_TEMPLATE = "https://apim.hoyts.com.au/au/cinemaapi/api/sessions/{cinema_id}"
SEATS_URL_TEMPLATE = "https://apim.hoyts.com.au/au/ticketing/api/v1/ticket/seats/{cinema_id}/{session_id}"
TICKET_URL_TEMPLATE = "https://apim.hoyts.com.au/au/ticketing/api/v1/ticket/{cinema_id}/{session_id}"

# ---------------- HEADERS ----------------
def get_random_ip():
    # Random X-Forwarded-For to look like different clients
    return ".".join(str(random.randint(1, 255)) for _ in range(4))

def get_headers():
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Origin": "https://www.hoyts.com.au",
        "Referer": "https://www.hoyts.com.au/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "DNT": "1",
        "Pragma": "no-cache",
        "X-Forwarded-For": get_random_ip(),
    }

scraper = cloudscraper.create_scraper()

# ---------------- HELPERS ----------------
async def fetch_json(url):
    def _sync_fetch():
        try:
            r = scraper.get(url, headers=get_headers(), timeout=30)
            if r.status_code == 200:
                return r.json()
            else:
                return {"__error__": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"__error__": str(e)}
    return await asyncio.to_thread(_sync_fetch)

async def fetch_cinemas():
    return await fetch_json(CINEMAS_URL)

async def fetch_movies():
    data = await fetch_json(MOVIES_URL) or []
    if isinstance(data, dict) and "__error__" in data:
        print(f"⚠️ Movies fetch error: {data['__error__']}")
        return {}
    return {
        m["vistaId"]: {
            "name": m.get("name"),
            "summary": m.get("summary"),
            "duration": m.get("duration"),
            "releaseDate": m.get("releaseDate"),
            "posterImage": m.get("posterImage")
        }
        for m in data
    }

async def fetch_sessions(cinema_id):
    url = SESSIONS_URL_TEMPLATE.format(cinema_id=cinema_id)
    sessions = await fetch_json(url) or []
    if isinstance(sessions, dict) and "__error__" in sessions:
        return []
    if not ALL_MOVIES:
        sessions = [s for s in sessions if s.get("movieId") in TARGET_MOVIE_IDS]
    return sessions

async def fetch_adult_price(cinema_id, session_id, sem):
    async with sem:
        url = TICKET_URL_TEMPLATE.format(cinema_id=cinema_id, session_id=session_id)
        data = await fetch_json(url)
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

async def fetch_seat_stats(cinema_id, sess, price, sem):
    async with sem:
        url = SEATS_URL_TEMPLATE.format(cinema_id=cinema_id, session_id=sess["id"])
        data = await fetch_json(url) or {}
        if isinstance(data, dict) and "__error__" in data:
            raise Exception(data["__error__"])
        total = sold = 0
        for row in data.get("rows", []):
            for seat in row.get("seats", []):
                total += 1
                if seat.get("sold"):
                    sold += 1
        available = total - sold
        return {
            "total": total,
            "sold": sold,
            "available": available,
            "occupancy": round((sold / total * 100) if total else 0, 2),
            "max_gross": total * price,
            "total_gross": sold * price,
            "price": price,
        }

async def process_session(cinema_id, sess, sem):
    try:
        price = await fetch_adult_price(cinema_id, sess["id"], sem)
        stats = await fetch_seat_stats(cinema_id, sess, price, sem)
        return {
            "id": sess["id"],
            "cinemaId": cinema_id,
            "movieId": sess["movieId"],
            "date": sess.get("showDate") or sess.get("date"),
            "screenName": sess.get("screenName"),
            "operator": sess.get("operator"),
            **stats,
            "error": False,
        }
    except Exception as e:
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
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

    cinemas = await fetch_cinemas()
    movies_map = await fetch_movies()

    cinema_ids = [c["id"] for c in cinemas if isinstance(c, dict) and "id" in c]
    print(f"🎬 Total cinemas found: {len(cinema_ids)}")

    all_sessions = []
    for cinema_id in cinema_ids:
        sessions = await fetch_sessions(cinema_id)
        for s in sessions:
            s["cinemaId"] = cinema_id
        all_sessions.extend(sessions)
    print(f"📊 Total sessions fetched: {len(all_sessions)}")

    tasks = [process_session(s["cinemaId"], s, sem) for s in all_sessions]
    all_results = []
    for f in tqdm_asyncio.as_completed(tasks, total=len(tasks), desc="Fetching seats & prices"):
        try:
            res = await f
            if res:
                all_results.append(res)
        except Exception as e:
            print(f"⚠️ Session failed: {e}")

    # group and save like before (same logic as your code)
    sessions_by_date = defaultdict(dict)
    for r in all_results:
        show_date = r.get("date")
        show_date = show_date.split("T")[0] if show_date else "unknown"
        sessions_by_date[show_date][str(r["id"])] = r

    for show_date, data_dict in sessions_by_date.items():
        diff_path = f"Australia Data/{show_date}-data.json"
        existing_data = load_existing_data(diff_path)

        for sid, record in data_dict.items():
            if record.get("error"):
                if sid not in existing_data:
                    existing_data[sid] = record
            else:
                existing_data[sid] = record

        save_data(diff_path, existing_data)
        print(f"✅ Diff saved to {diff_path}")

        # build summary
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
            if movie in movies_map:
                agg.update(movies_map[movie])
            agg["id"] = movie
            summary_list.append(agg)

        summary_path = f"Australia Data/{show_date}-summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary_list, f, indent=2)
        print(f"📊 Summary saved to {summary_path}")

if __name__ == "__main__":
    asyncio.run(main())
