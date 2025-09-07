import requests, re, json, html, os
from datetime import datetime
from zoneinfo import ZoneInfo

VENUES = [
    "https://omniwebticketing5.com/central-parkway/",
    "https://omniwebticketing5.com/orleans/",
    "https://omniwebticketing5.com/woodside/",
    "https://omniwebticketing5.com/york/",
    "https://omniwebticketing5.com/albion/",
    "https://omniwebticketing5.com/garden-city/",
    "https://omniwebticketing2.com/theatre6/",
]

SCH_DATE = "2025-09-24"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})
session.headers["Cookie"] = "zenid=17b303d650abf2ce5426bf41c0498570"


# --- utils ---
def extract_gmoviedata(html_text):
    start = html_text.find("var gMovieData =")
    if start == -1:
        with open("debug.html", "w", encoding="utf-8") as f:
            f.write(html_text)
        raise ValueError("gMovieData not found!")

    snippet = html_text[start:]
    end = snippet.find("</script>")
    if end == -1:
        end = snippet.find("};") + 1
    raw_json = snippet[len("var gMovieData ="):end].strip().rstrip(";")
    clean_json = html.unescape(raw_json)
    return json.loads(clean_json)


def extract_security_token(html_text):
    m = re.search(r'name="securityToken"\s+value="([a-f0-9]+)"', html_text, re.I)
    if not m:
        raise ValueError("securityToken not found")
    return m.group(1)


def extract_counts(html_text):
    sold = len(re.findall(r'class="ow-cb[^"]*ow-sp', html_text))  # sold
    blocked = len(re.findall(r'class="ow-cb[^"]*ow-hs', html_text))     # blocked
    available = len(re.findall(r'class="ow-cb[^"]*ow-cb-av', html_text))
    total = sold + blocked + available
    return available, blocked, sold, total


def extract_pricing(html_text):
    m_service = re.search(r'id="ctl00_serviceTotalPureStr"\s+value="([\d.]+)"', html_text)
    m_grand  = re.search(r'id="ctl00_grandTotalPureStr"\s+value="([\d.]+)"', html_text)
    service_fee = float(m_service.group(1)) if m_service else 0.0
    grand_total = float(m_grand.group(1)) if m_grand else 0.0
    mtax = re.search(r'Taxes.*?>\s*([0-9]+\.[0-9]{2})\s*</span>', html_text, re.I|re.S)
    taxes = float(mtax.group(1)) if mtax else 0.0

    net = grand_total - taxes - service_fee if grand_total else 0.0
    return {
        "net": round(net, 2),
        "tax": round(taxes, 2),
        "fee": round(service_fee, 2),
        "grand": round(net + taxes + service_fee, 2)
    }


def goto_seatmap(base, sch_date, perf_ix):
    perfix_url = f"{base}?schdate={sch_date}&perfix={perf_ix}"
    r1 = session.get(perfix_url); r1.raise_for_status()
    token = extract_security_token(r1.text)

    params = {"main_page": "shopping_cart"}
    form = {
        "securityToken": token,
        "ctl00_dd_1": "1",  # 1 adult
        "ctl00_dd_2": "0",
        "ctl00_dd_3": "0",
        "ctl00_from_cats": "default",
        "ctl00_txtEmail": "",
        "ctl00_txtConfirmEmail": "",
    }
    r2 = session.post(perfix_url, params=params, data=form, allow_redirects=False)

    if r2.is_redirect or r2.status_code in (301, 302):
        loc = r2.headers["Location"]
        if not loc.startswith("http"): 
            loc = base + loc.lstrip("?")
        r3 = session.get(loc)
    else:
        r3 = session.get(f"{base}?seats=1")

    r3.raise_for_status()
    return r3.text


# --- main flow ---
flat_list = []

for base in VENUES:
    url = f"{base}?schdate={SCH_DATE}"
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"⚠ Skipping {base} -> {e}")
        continue

    try:
        movie_data = extract_gmoviedata(resp.text)
    except Exception as e:
        print(f"⚠ {base} movie data error: {e}")
        continue

    for mid, movie in movie_data.items():
        title = movie["title"].strip()

        for aud_id, aud in movie["schAuds"].items():
            for time, perf in aud["schPerfsReserved"].items():
                perf_ix = perf["perfIx"]
                try:
                    seatmap_html = goto_seatmap(base, SCH_DATE, perf_ix)
                    available, blocked, sold, total = extract_counts(seatmap_html)
                    pricing = extract_pricing(seatmap_html)
                    gross = round(sold * pricing["net"], 2)
                    gross_with_tax = round(sold * pricing["grand"], 2)

                    flat_list.append({
                        "venue": base.rstrip("/").split("/")[-1],
                        "movie": title,
                        "perfIx": perf_ix,
                        "date": perf["schDateStr"],
                        "time": perf["startTimeStr"],
                        "total": total,
                        "available": available,
                        "blocked": blocked,
                        "sold": sold,
                        "gross": gross,
                        "gross_with_tax": gross_with_tax,
                        "per_ticket": pricing
                    })

                    print(f"✅ {title} {perf['startTimeStr']} ({base}) -> Sold {sold}, Gross {gross_with_tax}")
                except Exception as e:
                    print(f"⚠ {title} {perf['startTimeStr']} ({base}) failed: {e}")


# --- save/load helpers ---
def load_existing(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠ Failed to load existing JSON: {e}")
    return []


def save_merged(path, old_data, new_data, log_path=None):
    # index old data by unique key (tuple)
    index = {(d["venue"], d["movie"], d["perfIx"], d["date"], d["time"]): d for d in old_data}

    # update or insert new shows
    for d in new_data:
        key = (d["venue"], d["movie"], d["perfIx"], d["date"], d["time"])
        index[key] = d  # overwrite if exists

    merged = list(index.values())

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    print(f"✅ Merged & saved {len(merged)} shows to {path}")

    # also update logs if path provided
    if log_path:
        update_logs(merged, log_path)


def update_logs(flat_list, log_path):
    # aggregate calculations
    total_gross = sum(d["gross_with_tax"] for d in flat_list)
    total_shows = len(flat_list)
    tickets_sold = sum(d["sold"] for d in flat_list)
    total_capacity = sum(d["total"] for d in flat_list)
    avg_occupancy = (tickets_sold / total_capacity * 100) if total_capacity else 0.0
    unique_venues = len(set(d["venue"] for d in flat_list))

    log_entry = {
        "time": datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %I:%M:%S %p"),
        "total_gross_usd": round(total_gross, 2),
        "total_shows": total_shows,
        "avg_occupancy": round(avg_occupancy, 2),
        "tickets_sold": tickets_sold,
        "unique_venues": unique_venues,
    }

    # load old logs if present
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except:
            logs = []
    else:
        logs = []

    logs.append(log_entry)

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)

    print(f"📊 Log appended to {log_path}")


# --- after scraping ---
out_dir = "Canada Data"
os.makedirs(out_dir, exist_ok=True)

out_file = os.path.join(out_dir, f"{SCH_DATE}_json.json")
log_file = os.path.join(out_dir, f"{SCH_DATE}_logs.json")

old_list = load_existing(out_file)
save_merged(out_file, old_list, flat_list, log_path=log_file)
