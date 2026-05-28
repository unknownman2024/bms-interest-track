import requests
from bs4 import BeautifulSoup
import json
import os
import logging
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# =========================================================
# CONFIG
# =========================================================
BASE_DIR = "Finland Data"
LEGACY_FILE = os.path.join(BASE_DIR, "data.json")

TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

HTML_URL = "https://www.myyrikino.fi/ohjelmisto/"
API_URL = "https://myyri.kinola.ee/api/plugin/v1/events/{uuid}/fi"

# Current Month-Year file
NOW = datetime.now()
MONTH_YEAR_FILE = f"{NOW.strftime('%m-%Y')}.json"
OUTPUT_FILE = os.path.join(BASE_DIR, MONTH_YEAR_FILE)

# =========================================================
# LOGGING
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%d-%m-%Y %H:%M:%S",
)

# =========================================================
# HTTP SESSION WITH RETRIES
# =========================================================
session = requests.Session()
retry_strategy = Retry(
    total=5,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

session.headers.update({"User-Agent": USER_AGENT})


# =========================================================
# FILE HELPERS
# =========================================================
def safe_load_json(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Corrupted JSON skipped: {path} | {e}")
    return []


def save_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# =========================================================
# DATETIME FORMATTER
# =========================================================
def parse_datetime(start_date):
    try:
        dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except Exception:
        return None, None


# =========================================================
# 🔁 ONE-TIME LEGACY MIGRATION
# =========================================================
def migrate_legacy_file():
    if not os.path.exists(LEGACY_FILE):
        logging.info("✅ No legacy data.json found. Migration skipped.")
        return

    logging.warning("⚠️ Legacy data.json found. Migration started...")

    legacy_data = safe_load_json(LEGACY_FILE)
    if not legacy_data:
        logging.warning("Legacy file empty. Deleting it.")
        os.remove(LEGACY_FILE)
        return

    monthly_buckets = {}

    for item in legacy_data:
        try:
            showtime = item.get("showtime")
            if showtime:
                date, time = parse_datetime(showtime)
            else:
                date = item.get("date")
                time = item.get("time")

            if not date:
                continue

            month_key = datetime.strptime(date, "%Y-%m-%d").strftime("%m-%Y")

            item["date"] = date
            item["time"] = time
            item.pop("showtime", None)

            monthly_buckets.setdefault(month_key, []).append(item)

        except Exception as e:
            logging.error(f"❌ Migration failed for one record: {e}")

    for month_key, records in monthly_buckets.items():
        month_file = os.path.join(BASE_DIR, f"{month_key}.json")
        existing = safe_load_json(month_file)

        index = {
            (s["id"], s.get("date"), s.get("time")): s
            for s in existing if "id" in s
        }

        for show in records:
            key = (show["id"], show.get("date"), show.get("time"))
            index[key] = show

        save_json_atomic(month_file, list(index.values()))
        logging.info(f"✅ Migrated → {month_file}")

    os.remove(LEGACY_FILE)
    logging.warning("🗑️ Legacy data.json deleted after successful migration")


# =========================================================
# SCRAPING FUNCTIONS
# =========================================================
def fetch_show_data():
    logging.info(f"🌍 Fetching HTML page: {HTML_URL}")
    resp = session.get(HTML_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    checkout_urls = [
        a["href"] for a in soup.find_all("a", href=True) if "checkout/" in a["href"]
    ]

    logging.info(f"✅ Found {len(checkout_urls)} checkout URLs")
    return checkout_urls


def fetch_api_data(uuid):
    api_url = API_URL.format(uuid=uuid)
    logging.info(f"→ Fetching API: {uuid}")

    r = session.get(api_url, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    price = data.get("ticketTypes", [{}])[0].get("price", 0)
    sold = len(data["seats"].get("unavailable", []))
    free = data["seats"].get("freeCount", 0)
    total = sold + free
    gross = round(sold * price, 2)
    occupancy = round((sold / total) * 100, 2) if total else 0

    movie_name = data["production"]["name"]
    poster = data["production"].get("image", {}).get("srcset")

    date, time = parse_datetime(data["details"]["startDate"])

    logging.info(
        f"🎬 {movie_name} | Sold: {sold}/{total} | Gross: €{gross} | Occ: {occupancy}%"
    )

    return {
        "id": uuid,
        "movie": movie_name,
        "date": date,
        "time": time,
        "price": price,
        "sold": sold,
        "free": free,
        "total": total,
        "gross": gross,
        "occupancy": occupancy,
        "poster": poster,
        "status": "ok",
        "lastUpdated": datetime.utcnow().isoformat()
    }


# =========================================================
# MERGE LOGIC
# =========================================================
def update_data(existing, new_data):
    index = {
        (s["id"], s.get("date"), s.get("time")): s
        for s in existing if "id" in s
    }

    for show in new_data:
        key = (show["id"], show.get("date"), show.get("time"))
        index[key] = show

    new_keys = {(s["id"], s.get("date"), s.get("time")) for s in new_data}

    for key, show in index.items():
        if key not in new_keys and show.get("status") == "ok":
            show["status"] = "missing"

    return list(index.values())


# =========================================================
# MAIN
# =========================================================
def main():
    logging.info("🚀 Script Started")

    # 🔁 Run migration once
    migrate_legacy_file()

    existing = safe_load_json(OUTPUT_FILE)
    urls = fetch_show_data()
    new_results = []

    for url in urls:
        uuid = url.split("checkout/")[-1].strip("/")

        try:
            show = fetch_api_data(uuid)
            new_results.append(show)
        except Exception as e:
            logging.error(f"❌ Failed: {uuid} | {e}")
            new_results.append({
                "id": uuid,
                "status": "error",
                "error": str(e),
                "lastUpdated": datetime.utcnow().isoformat()
            })

    merged = update_data(existing, new_results)
    save_json_atomic(OUTPUT_FILE, merged)

    logging.info(
        f"✅ Finished. Updated: {len(new_results)} | Total Stored: {len(merged)}"
    )


if __name__ == "__main__":
    main()
