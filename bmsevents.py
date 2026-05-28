import cloudscraper
import requests
import json
from collections import defaultdict

def fetch_city_data(city_slug):
    scraper = cloudscraper.create_scraper()
    
    homepage_url = f"https://in.bookmyshow.com/explore/home/{city_slug}"
    print(f"[*] Accessing BookMyShow homepage for {city_slug}...")
    homepage_response = scraper.get(homepage_url)
    if homepage_response.status_code != 200:
        print(f"[!] Failed to access homepage for {city_slug}")
        return None

    json_url = "https://in.bookmyshow.com/serv/getData?cmd=QUICKBOOK&type=MT"
    print(f"[*] Fetching movie JSON data for {city_slug}...")
    json_response = scraper.get(json_url)
    if json_response.status_code != 200:
        print(f"[!] Failed to get JSON for {city_slug}")
        return None

    try:
        return json.loads(json_response.text)
    except:
        print(f"[!] Invalid JSON for {city_slug}")
        return None

def extract_movies(data):
    result = {}
    movies = data['moviesData']['BookMyShow']['arrEvents']

    for movie in movies:
        title = movie.get('EventTitle')
        child_events = movie.get('ChildEvents', [])
        if not child_events:
            continue

        first_variant = child_events[0]
        main_poster = f"https://in.bmscdn.com/events/moviecard/{first_variant.get('EventImageCode')}.jpg"
        main_genres = first_variant.get("Genre", [])
        main_rating = first_variant.get("EventCensor")
        main_duration = first_variant.get("Duration")
        main_event_date = first_variant.get("EventDate")
        main_is_new = first_variant.get("isNewEvent")

        if title not in result:
            result[title] = {
                "Title": title,
                "Poster": main_poster,
                "Genres": main_genres,
                "Rating": main_rating,
                "Duration": main_duration,
                "EventDate": main_event_date,
                "isNewEvent": main_is_new,
                "Variants": []
            }

        existing_event_codes = {v["EventCode"] for v in result[title]["Variants"]}

        for variant in child_events:
            code = variant.get("EventCode")
            if code not in existing_event_codes:
                variant_info = {
                    "VariantName": variant.get("EventName"),
                    "EventCode": code,
                    "Language": variant.get("EventLanguage"),
                    "Format": variant.get("EventDimension")
                }
                result[title]["Variants"].append(variant_info)

    return result

def merge_paytm_data(all_movies_dict):
    print("[*] Fetching Paytm movie data...")
    url = "https://paytmmovies.text2024mail.workers.dev/"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        paytm_data = response.json()
    except Exception as e:
        print(f"[!] Failed to fetch Paytm data: {e}")
        return

    for entry in paytm_data:
        paytm_title = entry["movie"].strip().lower()
        # Skip if required fields are missing
        required_fields = ["movie", "movieCode", "id", "language"]
        if not all(field in entry for field in required_fields):
            continue

        variant_info = {
            "movieCode": entry["movieCode"],
            "id": entry["id"],
            "language": entry["language"]
        }

        for bms_title in all_movies_dict:
            if bms_title.strip().lower() == paytm_title:
                if "PaytmVariants" not in all_movies_dict[bms_title]:
                    all_movies_dict[bms_title]["PaytmVariants"] = []

                existing = all_movies_dict[bms_title]["PaytmVariants"]
                if not any(v["movieCode"] == variant_info["movieCode"] for v in existing):
                    all_movies_dict[bms_title]["PaytmVariants"].append(variant_info)

    print("[*] Paytm variants merged where matched.")

def load_city_slugs(filename="cities.json"):
    with open(filename, "r", encoding="utf-8") as f:
        cities = json.load(f)
    return [city["RegionSlug"] for city in cities]

if __name__ == "__main__":
    all_movies = {}
    city_slugs = load_city_slugs()

    for city_slug in city_slugs:
        data = fetch_city_data(city_slug)
        if not data:
            continue

        city_movies = extract_movies(data)
        for title, movie_info in city_movies.items():
            if title not in all_movies:
                all_movies[title] = movie_info
            else:
                # merge unique variants
                existing_codes = {v["EventCode"] for v in all_movies[title]["Variants"]}
                for v in movie_info["Variants"]:
                    if v["EventCode"] not in existing_codes:
                        all_movies[title]["Variants"].append(v)

    # Merge Paytm movie variants
    merge_paytm_data(all_movies)

    # Final output
    final_list = list(all_movies.values())

    with open("moviesdata.json", "w", encoding="utf-8") as f:
        json.dump(final_list, f, indent=4, ensure_ascii=False)

    print(f"\n✅ Done. {len(final_list)} unique movies saved to all_movies_grouped_clean.json")
