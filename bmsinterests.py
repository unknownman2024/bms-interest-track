#!/usr/bin/env python3
"""
Final corrected BMS fetcher script (IST version).

- Uses API: https://upcomingbms.text2024mail.workers.dev/?pages=10
- All files under "Bookmyshow Data/" folder
- Movies entries are ordered by releaseDate (earliest first).
- Each movie's keys keep insertion order (no JSON key-sorting).
- Monthly log file is auto-created with a non-empty JSON so GitHub tracks it.
- Spike detection requires BOTH: percent threshold and absolute increase.
- ALL timestamps are in IST (UTC+5:30).
- Console logs printed for CI visibility.
"""

import json
import requests
import os
import time
from datetime import datetime, timedelta, timezone
from collections import OrderedDict

# -----------------------
# Config (IST)
# -----------------------
IST = timezone(timedelta(hours=5, minutes=30))

BASE_FOLDER = "Bookmyshow Data"
API_URL = "https://upcomingbms.text2024mail.workers.dev/?pages=10"

MOVIES_DB_FILE = os.path.join(BASE_FOLDER, "moviesdb.json")
LAST_FETCH_FILE = os.path.join(BASE_FOLDER, "lastFetch.json")
METADATA_FILE = os.path.join(BASE_FOLDER, "metadata.json")
LOGS_FOLDER = os.path.join(BASE_FOLDER, "logs")
RELEASE_LOG_FILE = os.path.join(BASE_FOLDER, "logs_release_changes.json")
SPIKE_LOG_FILE = os.path.join(BASE_FOLDER, "logs_spikes.json")

now = datetime.now(IST)
timestamp = now.strftime("%Y-%m-%d %H:%M")
month_key = now.strftime("%m-%Y")
month_log_file = os.path.join(LOGS_FOLDER, f"{month_key}.json")

# Spike config
PERCENT_SPIKE = 50
ABS_SPIKE = 1000

# Retry settings
API_RETRIES = 3
API_TIMEOUT = 15
API_BACKOFF = 2

os.makedirs(BASE_FOLDER, exist_ok=True)
os.makedirs(LOGS_FOLDER, exist_ok=True)

# -----------------------
# Helpers
# -----------------------
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data, sort_keys=False):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=sort_keys)


def safe_get(url, retries=API_RETRIES, timeout=API_TIMEOUT, backoff=API_BACKOFF):
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            if attempt < retries:
                time.sleep(backoff * attempt)
            else:
                return {"movies": []}


def normalize_release_date(rd):
    if not rd or not isinstance(rd, str):
        return None
    rd = rd.strip()
    if rd.startswith("20") and len(rd) >= 10:
        return rd
    return None


def movie_sort_key(movie):
    rd = movie.get("releaseDate")
    return rd if rd else "9999-12-31"


# -----------------------
# Main Logic
# -----------------------
def run():

    # Ensure monthly log file exists
    if not os.path.isfile(month_log_file):
        with open(month_log_file, "w", encoding="utf-8") as f:
            json.dump({"_created": timestamp}, f, indent=2)
        print(f"[MONTH LOG] Created new file: {month_log_file}")

    # Load persistent data
    moviesdb = load_json(MOVIES_DB_FILE, {})
    lastFetch = load_json(LAST_FETCH_FILE, {})
    release_changes = load_json(RELEASE_LOG_FILE, {})
    spike_logs = load_json(SPIKE_LOG_FILE, {})
    month_logs = load_json(month_log_file, {})

    if not isinstance(month_logs, dict):
        month_logs = {}

    # Fetch API
    data = safe_get(API_URL)
    movies = data.get("movies", []) or []

    logs_written = 0
    spikes_found = 0
    release_changes_count = 0
    fetched_movies = 0

    for mov in movies:
        fetched_movies += 1
        code = mov.get("movieCode")
        if not code:
            continue

        # ---- DATA ----
        movie_name = mov.get("movieName")
        languages = mov.get("languages") or []
        genres = mov.get("genres") or []
        poster = mov.get("poster")
        poster_with_release = mov.get("posterWithReleaseDate")
        rd = normalize_release_date(mov.get("releaseDate"))

        # Likes
        try:
            likes = int(mov.get("likes") or 0)
        except:
            likes = 0

        # ---- UPSERT DB ----
        if code not in moviesdb:
            moviesdb[code] = {
                "movieName": movie_name,
                "languages": languages,
                "genres": genres,
                "movieCode": code,
                "poster": poster,
                "posterWithReleaseDate": poster_with_release,
                "releaseDate": rd,
                "likes": likes
            }
        else:
            old_rd = moviesdb[code].get("releaseDate")

            # Update
            moviesdb[code]["movieName"] = movie_name
            moviesdb[code]["languages"] = languages
            moviesdb[code]["genres"] = genres
            moviesdb[code]["poster"] = poster
            moviesdb[code]["posterWithReleaseDate"] = poster_with_release

            # Release date change detection
            if old_rd != rd and old_rd is not None:
                if code not in release_changes:
                    release_changes[code] = {}

                release_changes[code][timestamp] = {"old": old_rd, "new": rd}
                release_changes_count += 1

            moviesdb[code]["releaseDate"] = rd

        # ---- MONTHLY LOGS ----
        old_likes = lastFetch.get(code, {}).get("likes", 0)
        new_likes = likes

        is_first_month = code not in month_logs or not isinstance(month_logs.get(code), dict) or len(month_logs.get(code)) == 0
        likes_changed = (old_likes != new_likes)

        if is_first_month or likes_changed:
            if code not in month_logs or not isinstance(month_logs.get(code), dict):
                month_logs[code] = {}

            month_logs[code][timestamp] = new_likes
            logs_written += 1

        # ---- SPIKE DETECTION (uses previous-run old_likes) ----
        old_safe = old_likes if isinstance(old_likes, int) and old_likes > 0 else 1
        abs_change = new_likes - old_safe
        percent_change = (abs_change / old_safe) * 100 if old_safe else 0

        is_spike = abs_change >= ABS_SPIKE and percent_change >= PERCENT_SPIKE

        if is_spike:
            if code not in spike_logs or not isinstance(spike_logs.get(code), dict):
                spike_logs[code] = {}

            spike_logs[code][timestamp] = {
                "old": old_safe,
                "new": new_likes,
                "abs_change": abs_change,
                "percent_change": round(percent_change, 2)
            }
            spikes_found += 1
            print(f"[SPIKE] {code} {movie_name} → +{abs_change} ({percent_change:.2f}%) IST")

        # ---- LASTFETCH ----
        moviesdb[code]["likes"] = new_likes

        lastFetch[code] = {
            "movieName": movie_name,
            "languages": languages,
            "genres": genres,
            "movieCode": code,
            "poster": poster,
            "posterWithReleaseDate": poster_with_release,
            "releaseDate": rd,
            "likes": new_likes,
            "fetchedAt": timestamp
        }

    # Sort DB
    sorted_items = sorted(moviesdb.items(), key=lambda x: movie_sort_key(x[1]))
    moviesdb_ordered = OrderedDict(sorted_items)

    # ---- FIXED MONTHLY TOTAL (skip metadata keys) ----
    month_log_total = sum(
        len(v) for v in month_logs.values()
        if isinstance(v, dict)
    ) if month_logs else 0

    total_spikes = sum(len(v) for v in spike_logs.values() if isinstance(v, dict))
    total_release_changes = sum(len(v) for v in release_changes.values() if isinstance(v, dict))

    # ---- METADATA ----
    metadata = load_json(METADATA_FILE, {})
    metadata.update({
        "lastRunIST": timestamp,
        "fetchedMovies": fetched_movies,
        "totalMovies": len(moviesdb_ordered),
        "logEntriesThisRun": logs_written,
        "logEntriesThisMonth": month_log_total,
        "spikesDetectedThisRun": spikes_found,
        "totalSpikes": total_spikes,
        "releaseDateChangesThisRun": release_changes_count,
        "totalReleaseDateChanges": total_release_changes
    })

    # ---- SAVE ----
    save_json(MOVIES_DB_FILE, moviesdb_ordered)
    save_json(month_log_file, month_logs)
    save_json(LAST_FETCH_FILE, lastFetch)
    save_json(RELEASE_LOG_FILE, release_changes)
    save_json(SPIKE_LOG_FILE, spike_logs)
    save_json(METADATA_FILE, metadata)

    # ---- SUMMARY ----
    print("\n===== RUN SUMMARY =====")
    print(f"IST Time: {timestamp}")
    print(f"Fetched movies: {fetched_movies}")
    print(f"Total movies in DB: {len(moviesdb_ordered)}")
    print(f"Log entries written this run: {logs_written}")
    print(f"Spikes detected this run: {spikes_found}")
    print(f"Release date changes this run: {release_changes_count}")
    print(f"Monthly log file: {month_log_file} (entries: {month_log_total})")
    print("=======================\n")


if __name__ == "__main__":
    run()
