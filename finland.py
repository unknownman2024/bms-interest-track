import requests
from bs4 import BeautifulSoup
import json
import os

OUTPUT_FILE = "Finland Data/data.json"

def load_existing():
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_data(data):
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def fetch_show_data():
    html_url = "https://www.myyrikino.fi/ohjelmisto/"
    print(f"🌍 Fetching HTML page: {html_url}")
    resp = requests.get(html_url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    checkout_urls = [a["href"] for a in soup.find_all("a", href=True) if "checkout/" in a["href"]]
    print(f"✅ Found {len(checkout_urls)} checkout URLs")
    return checkout_urls

def fetch_api_data(uuid):
    api_url = f"https://myyri.kinola.ee/api/plugin/v1/events/{uuid}/fi"
    print(f"   → Fetching API data for {uuid}")
    r = requests.get(api_url, timeout=10)
    r.raise_for_status()
    data = r.json()

    price = data["ticketTypes"][0]["price"] if data.get("ticketTypes") else 0
    sold = len(data["seats"]["unavailable"])
    free = data["seats"]["freeCount"]
    total = sold + free
    gross = sold * price
    occupancy = round((sold / total) * 100, 2) if total else 0

    print(f"      🎬 {data['production']['name']} | Sold: {sold}/{total} | Gross: {gross}€ | Occ: {occupancy}%")

    return {
        "id": uuid,
        "movie": data["production"]["name"],
        "showtime": data["details"]["startDate"],
        "price": price,
        "sold": sold,
        "free": free,
        "total": total,
        "gross": gross,
        "occupancy": occupancy,
        "poster": data["production"]["image"]["srcset"],
        "status": "ok"
    }

def update_data(existing, new_data):
    index = { (s["id"], s.get("showtime"), s.get("movie")): s for s in existing }

    for show in new_data:
        key = (show["id"], show.get("showtime"), show.get("movie"))
        index[key] = show  # update or insert

    new_keys = {(s["id"], s.get("showtime"), s.get("movie")) for s in new_data}
    for key, show in index.items():
        if key not in new_keys and show.get("status") == "ok":
            show["status"] = "missing"
            print(f"⚠️ Marking missing: {show['movie']} ({show['showtime']})")

    return list(index.values())

if __name__ == "__main__":
    existing = load_existing()
    urls = fetch_show_data()
    new_results = []

    for url in urls:
        uuid = url.split("checkout/")[-1].strip("/")
        try:
            show = fetch_api_data(uuid)
            new_results.append(show)
        except Exception as e:
            print(f"   ❌ Error fetching {uuid}: {e}")
            new_results.append({
                "id": uuid,
                "error": str(e),
                "status": "error"
            })

    merged = update_data(existing, new_results)
    save_data(merged)

    print(f"\n✅ Finished. Updated {len(new_results)} shows, total saved: {len(merged)} → {OUTPUT_FILE}")
