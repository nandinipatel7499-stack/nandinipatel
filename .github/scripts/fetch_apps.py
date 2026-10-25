import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEVELOPER_ID = "1742606172"
OUTPUT = os.environ.get("OUTPUT", "apps.json")
PREVIOUS_URL = os.environ.get("PREVIOUS_URL")
DISPLAY_STOREFRONT = "us"

STOREFRONTS = (
    "ae af ag ai al am ao ar at au az ba bb bd be bf bg bh bj bm bn bo br bs bt bw by bz "
    "ca cd cg ch ci cl cm cn co cr cv cy cz de dk dm do dz ec ee eg es fi fj fm fr ga gb gd ge gh gm gr gt gw gy "
    "hk hn hr hu id ie il in iq is it jm jo jp ke kg kh kn kr kw ky kz la lb lc lk lr lt lu lv ly ma md me mg mk "
    "ml mm mn mo mr ms mt mu mv mw mx my mz na ne ng ni nl no np nr nz om pa pe pg ph pk pl pt pw py qa ro rs ru "
    "rw sa sb sc se sg si sk sl sn sr st sv sz tc td th tj tm tn to tr tt tw tz ua ug us uy uz vc ve vg vn vu xk "
    "ye za zm zw"
).split()

DISPLAY_FIELDS = [
    "trackId", "trackName", "subtitle", "description", "artistName", "sellerName", "artistId",
    "primaryGenreName", "genres", "trackContentRating", "contentAdvisoryRating", "fileSizeBytes",
    "version", "releaseDate", "currentVersionReleaseDate", "releaseNotes", "minimumOsVersion",
    "price", "formattedPrice", "currency", "artworkUrl60", "artworkUrl100", "artworkUrl512",
    "screenshotUrls", "ipadScreenshotUrls", "languageCodesISO2A", "bundleId", "trackViewUrl",
]


def fetch_storefront(country):
    url = (
        f"https://itunes.apple.com/lookup?id={DEVELOPER_ID}&entity=software&limit=200"
        f"&country={country}&_cb={int(time.time())}"
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                data = json.load(response)
            apps = [r for r in data.get("results", []) if r.get("wrapperType") == "software" and r.get("trackId")]
            return country, apps
        except Exception as error:
            print(f"{country}: attempt {attempt + 1} failed ({error})", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    return country, None


def load_previous():
    if PREVIOUS_URL:
        try:
            request = urllib.request.Request(
                f"{PREVIOUS_URL}?t={int(time.time())}",
                headers={"User-Agent": "nandinipatel-apps-action/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except Exception as error:
            print(f"Could not load previous data ({error})", file=sys.stderr)
            return {}
    if not os.path.exists(OUTPUT):
        return {}
    try:
        with open(OUTPUT) as handle:
            return json.load(handle)
    except Exception:
        return {}


def main():
    previous = load_previous()
    previous_stores = previous.get("storefrontRatings", {})

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(pool.map(fetch_storefront, STOREFRONTS))

    stores = {}
    display = {}
    failed = []
    for country in STOREFRONTS:
        apps = results.get(country)
        if apps is None:
            failed.append(country)
            if country in previous_stores:
                stores[country] = previous_stores[country]
            continue
        stores[country] = {
            str(app["trackId"]): [app.get("userRatingCount") or 0, round(app.get("averageUserRating") or 0, 4)]
            for app in apps
        }
        for app in apps:
            app_id = str(app["trackId"])
            if country == DISPLAY_STOREFRONT or app_id not in display:
                display[app_id] = app

    if len(failed) > len(STOREFRONTS) // 2:
        print(f"Too many storefronts failed ({len(failed)}); keeping previous {OUTPUT}", file=sys.stderr)
        sys.exit(1)

    apps_out = []
    for app_id, app in display.items():
        count = 0
        weighted = 0.0
        for store in stores.values():
            entry = store.get(app_id)
            if entry:
                count += entry[0]
                weighted += entry[0] * entry[1]
        item = {field: app[field] for field in DISPLAY_FIELDS if field in app}
        item["userRatingCount"] = count
        item["averageUserRating"] = round(weighted / count, 4) if count else 0
        apps_out.append(item)

    apps_out.sort(key=lambda a: a.get("releaseDate", ""), reverse=True)

    output = {
        "developerId": DEVELOPER_ID,
        "storefrontsLoaded": len(STOREFRONTS) - len(failed),
        "storefrontsTotal": len(STOREFRONTS),
        "apps": apps_out,
        "storefrontRatings": dict(sorted(stores.items())),
    }

    comparable_old = {k: v for k, v in previous.items() if k != "updatedAt"}
    if comparable_old == output:
        print("No changes")
        return

    output = {"updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **output}
    with open(OUTPUT, "w") as handle:
        json.dump(output, handle, ensure_ascii=False, separators=(",", ":"))
    total = sum(a["userRatingCount"] for a in apps_out)
    print(f"Wrote {len(apps_out)} apps, {total} ratings, {output['storefrontsLoaded']}/{len(STOREFRONTS)} storefronts")


if __name__ == "__main__":
    main()
