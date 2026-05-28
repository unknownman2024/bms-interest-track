###############################################################
#  USA Movie Advance Show Scraper (Production Ready)
#  Designed for daily run — tracks NEXT 5 days continuously
#  Author: BFilmy Automation
###############################################################

import requests, json, os, random, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

###############################################################
# CONFIG
###############################################################

ZIP_FILE = "zipcodes.txt"
OUT_DIR = "USA_MOVIE_TRACKER"
THREADS = 20
RETRIES = 3
TIMEOUT = 10

# Fandango API endpoint
API_URL = "https://www.fandango.com/napi/theaterswithshowtimes"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{v} Safari/537.36"
]


###############################################################
# HELPERS
###############################################################

def rand_ua():
    return USER_AGENTS[0].format(v=f"{random.randint(100,130)}.0.{random.randint(1000,6000)}.{random.randint(1,200)}")

def rand_ip():
    return ".".join(str(random.randint(1,255)) for _ in range(4))

def headers(zipcode, date):
    ip = rand_ip()
    return {
        "User-Agent": rand_ua(),
        "Accept": "application/json",
        "Referer": f"https://www.fandango.com/{zipcode}_movietimes?date={date}",
        "X-Forwarded-For": ip,
        "Client-IP": ip
    }


###############################################################
# CORE SCRAPER
###############################################################

def fetch_zip(zipcode, date):
    """Fetch showtime data for ZIP + date with retries."""
    params = {"zipCode": zipcode, "date": date, "filter": "open-theaters", "filterEnabled": "true"}

    for attempt in range(RETRIES):
        try:
            r = requests.get(API_URL, headers=headers(zipcode, date), params=params, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json().get("theaters", [])
            
        except Exception:
            time.sleep(1)  # backoff
    
    return []


def extract_showtimes(movie):
    show_list = []

    for variant in movie.get("variants", []):
        base_format = variant.get("formatName", "").strip()

        for group in variant.get("amenityGroups", []):
            amenities = [a.get("name", "").strip() for a in group.get("amenities", [])]

            for show in group.get("showtimes", []):
                time_str = show.get("ticketingDateTimeFormatted", show.get("ticketingDate"))

                info = [time_str]

                if base_format:
                    info.append(base_format)

                if amenities:
                    info.append(",".join(amenities))

                show_list.append(" - ".join(info))

    return show_list


def process_day(date, zipcodes):
    """Scrape ALL movies across all ZIP codes for a single date."""
    print(f"\n🗓 Processing advance date: {date}")

    theaters_data = []

    with ThreadPoolExecutor(max_workers=THREADS) as exe:
        futures = {exe.submit(fetch_zip, z, date): z for z in zipcodes}

        for f in as_completed(futures):
            z = futures[f]
            try:
                data = f.result()
                if data:
                    print(f"📍 ZIP {z}: {len(data)} theaters found")
                theaters_data.extend(data)
            except:
                pass

    movies = {}

    for t in theaters_data:
        loc = f"{t.get('city')}, {t.get('state')}"
        theater = t.get("name")

        for movie in t.get("movies", []):
            mid = str(movie.get("id"))

            if mid not in movies:
                movies[mid] = {"total_shows": 0, "cities": {}}

            showtimes = extract_showtimes(movie)
            movies[mid]["total_shows"] += len(showtimes)

            if loc not in movies[mid]["cities"]:
                movies[mid]["cities"][loc] = {}

            movies[mid]["cities"][loc][theater] = showtimes

    return movies


###############################################################
# MAIN EXECUTION
###############################################################

def run():
    if not os.path.exists(ZIP_FILE):
        print("❌ zipcodes.txt missing!")
        return
    
    zipcodes = open(ZIP_FILE).read().splitlines()
    print(f"📦 Loaded {len(zipcodes)} ZIP codes")

    # Get USA current date
    today_usa = datetime.now(ZoneInfo("America/Los_Angeles")).date()

    # Rolling 5-day schedule
    target_dates = [(today_usa + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 6)]

    day_folder = os.path.join(OUT_DIR, str(today_usa))
    os.makedirs(day_folder, exist_ok=True)

    final_data = {}

    for d in target_dates:
        final_data[d] = process_day(d, zipcodes)

    # Save unified JSON
    output_file = os.path.join(day_folder, "shows.json")
    json.dump(final_data, open(output_file, "w"), indent=2)

    # Summary for dashboard
    summary = {
        "date_generated": str(today_usa),
        "tracked_dates": target_dates,
        "unique_movies": len({m for day in final_data.values() for m in day.keys()}),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    json.dump(summary, open(os.path.join(day_folder, "summary.json"), "w"), indent=2)

    print("\n✅ DONE! Saved:")
    print(f"📁 Data: {output_file}")
    print(f"📁 Summary: summary.json")


run()
