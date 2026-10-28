import json
import os
import re
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


BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"


def extract_preview_videos(html):
    videos, seen = [], set()
    for match in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.S):
        body = match.group(1).strip()
        if "product_media_" not in body or not body.startswith("{"):
            continue
        try:
            data = json.loads(body)
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if key.startswith("product_media_") and isinstance(value, dict):
                        device = "pad" if "pad" in key else "phone"
                        for item in value.get("items") or []:
                            video = (item or {}).get("video") or {}
                            url = video.get("videoUrl")
                            if not url or url in seen or not url.startswith("https://"):
                                continue
                            seen.add(url)
                            preview = video.get("preview") or {}
                            poster = None
                            template = preview.get("template")
                            if template and preview.get("width") and preview.get("height"):
                                poster = (
                                    template.replace("{w}", str(preview["width"]))
                                    .replace("{h}", str(preview["height"]))
                                    .replace("{c}", "bb")
                                    .replace("{f}", "png")
                                )
                            videos.append({"url": url, "poster": poster, "device": device})
                    stack.append(value)
            elif isinstance(node, list):
                stack.extend(node)
    return videos


def fetch_preview_videos(app_id):
    url = f"https://apps.apple.com/{DISPLAY_STOREFRONT}/app/id{app_id}"
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US"})
            with urllib.request.urlopen(request, timeout=30) as response:
                html = response.read().decode("utf-8", "replace")
            if "product_media_" not in html:
                raise ValueError("no media shelf in page")
            return app_id, extract_preview_videos(html)
        except Exception as error:
            print(f"previews {app_id}: attempt {attempt + 1} failed ({error})", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    return app_id, None


def fetch_store_order():
    url = f"https://apps.apple.com/{DISPLAY_STOREFRONT}/developer/id{DEVELOPER_ID}"
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US"})
            with urllib.request.urlopen(request, timeout=30) as response:
                html = response.read().decode("utf-8", "replace")
            order = list(dict.fromkeys(re.findall(r'"\$kind":"Lockup","title":"[^"]*","adamId":"?(\d+)', html)))
            if not order:
                raise ValueError("no app lockups in developer page")
            return order
        except Exception as error:
            print(f"store order: attempt {attempt + 1} failed ({error})", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    return None


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

    previous_videos = {str(a.get("trackId")): a.get("previewVideos") for a in previous.get("apps", [])}
    with ThreadPoolExecutor(max_workers=4) as pool:
        videos = dict(pool.map(fetch_preview_videos, list(display)))

    store_order = fetch_store_order()
    if store_order is None:
        store_order = [
            str(a.get("trackId"))
            for a in sorted(previous.get("apps", []), key=lambda a: a.get("storeRank", 10**6))
            if isinstance(a.get("storeRank"), int)
        ]

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
        app_videos = videos.get(app_id)
        if app_videos is None:
            app_videos = previous_videos.get(app_id) or []
        item["previewVideos"] = app_videos
        if app_id in store_order:
            item["storeRank"] = store_order.index(app_id)
        apps_out.append(item)

    apps_out.sort(key=lambda a: a.get("releaseDate", ""), reverse=True)
    apps_out.sort(key=lambda a: a.get("storeRank", -1))

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
