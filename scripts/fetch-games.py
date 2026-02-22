#!/usr/bin/env python3
"""
fetch-games.py

Downloads .swf files for app13.info by:
  1. Querying the Flashpoint Archive API to find each game and its original URL
  2. Trying to download the .swf directly from the original host
  3. Falling back to the Wayback Machine (Internet Archive) if the original is dead

Run from the repo root:
    python3 scripts/fetch-games.py

Already-downloaded files are skipped. Games that couldn't be found are
printed in a summary at the end.
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import os
import time
import sys
import ssl

# macOS Python 3 doesn't use system certs by default — use certifi if available
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

FLASHPOINT_API = "https://db-api.unstable.life"
WAYBACK_CDX    = "https://web.archive.org/cdx/search/cdx"
WAYBACK_RAW    = "https://web.archive.org/web/{timestamp}oe_/{url}"
GAMES_DIR      = os.path.join(os.path.dirname(__file__), "..", "games")

# Override search titles for slugs that don't convert cleanly to game names
TITLE_OVERRIDES = {
    "14303_vrdefendery3k":  "VR Defender Y3K",
    "1048_castle":          "1048 Castle",
    "alien-hominid":        "Alien Hominid",
    "bot-arena-2":          "Bot Arena 2",
    "bow-master-prelude":   "Bow Master Prelude",
    "bubble-tanks-2":       "Bubble Tanks 2",
    "bunny-invasion-2":     "Bunny Invasion 2",
    "copter":               "Helicopter Game",
    "crush-the-castle":     "Crush the Castle",
    "d-fence-2":            "D-Fence 2",
    "defend-your-castle":   "Defend Your Castle",
    "demonic-defence-3":    "Demonic Defence 3",
    "dolphin-olympics-2":   "Dolphin Olympics 2",
    "double-wires":         "Double Wires",
    "fancy-pants-adventure":   "The Fancy Pants Adventures",
    "fancy-pants-adventure-2": "The Fancy Pants Adventures: World 2",
    "gem-tower-defense":    "Gem Tower Defense",
    "gravity-game":         "Gravity Game",
    "gunmaster-jungle":     "Gunmaster Jungle",
    "hot-air-baloons":      "Hot Air Balloon",
    "interactive-buddy":    "Interactive Buddy",
    "line-rider-beta-2":    "Line Rider",
    "the-last-stand-2":     "The Last Stand 2",
    "madness-accelerant":   "Madness Accelerant",
    "max-dirtbike":         "Max Dirt Bike",
    "me2d-game":            "ME2D",
    "missile-game-3d":      "Missile Game 3D",
    "n-ninja":              "N (The Way of the Ninja)",
    "pet-protector-2":      "Pet Protector 2",
    "phage-wars":           "Phage Wars",
    "portal":               "Portal: The Flash Version",
    "raiden-x":             "Raiden X",
    "realm-of-the-mad-god": "Realm of the Mad God",
    "sonny-2":              "Sonny 2",
    "stick-strike":         "Stick Strike",
    "super-mario-flash":    "Super Mario Flash",
    "super-smash-flash":    "Super Smash Flash",
    "bloons-tower-defense-3": "Bloons Tower Defense 3",
    "bloons-tower-defense-4": "Bloons Tower Defense 4",
}

GAMES = [
    "14303_vrdefendery3k",
    "1048_castle",
    "adrenaline",
    "alien-hominid",
    "alpha-bravo-charlie",
    "archer",
    "asteroids",
    "avalanche",
    "bloons-tower-defense-3",
    "bloons-tower-defense-4",
    "bot-arena-2",
    "bowman",
    "bow-master-prelude",
    "bubble-tanks-2",
    "bubble-tanks",
    "bubble-trouble",
    "bunny-invasion-2",
    "copter",
    "cubefield",
    "curveball",
    "crush-the-castle",
    "d-fence-2",
    "defend-your-castle",
    "demonic-defence-3",
    "dolphin-olympics-2",
    "double-wires",
    "fancy-pants-adventure-2",
    "fancy-pants-adventure",
    "feudalism-2",
    "fishy",
    "gem-tower-defense",
    "gravity-game",
    "gunmaster-jungle",
    "helicopter",
    "hot-air-baloons",
    "interactive-buddy",
    "line-rider-beta-2",
    "the-last-stand-2",
    "manhattan-project",
    "madness-accelerant",
    "max-dirtbike",
    "me2d-game",
    "missile-game-3d",
    "n-ninja",
    "pet-protector-2",
    "phage-wars",
    "portal",
    "raiden-x",
    "realm-of-the-mad-god",
    "run",
    "sonny-2",
    "sonny",
    "stairfall",
    "stick-strike",
    "super-mario-flash",
    "super-smash-flash",
    "tanks",
    "trampoline",
]

HEADERS = {
    "User-Agent": "app13.info-game-fetcher/1.0 (https://github.com/a-bissell/app13.info)"
}

def slug_to_title(slug):
    if slug in TITLE_OVERRIDES:
        return TITLE_OVERRIDES[slug]
    return slug.replace("-", " ").replace("_", " ").title()

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return resp.read()
    except Exception:
        return None

def search_flashpoint(title):
    params = urllib.parse.urlencode({
        "title": title,
        "fields": "id,title,platform,launchCommand",
        "limit": "15",
    })
    data = http_get(f"{FLASHPOINT_API}/search?{params}")
    if not data:
        return None
    try:
        results = json.loads(data)
    except Exception:
        return None

    if not results:
        return None

    title_lower = title.lower()

    # 1. Exact title + Flash
    for r in results:
        if (r.get("title", "").lower() == title_lower
                and "flash" in r.get("platform", "").lower()):
            return r

    # 2. Exact title any platform
    for r in results:
        if r.get("title", "").lower() == title_lower:
            return r

    # 3. Any Flash result
    for r in results:
        if "flash" in r.get("platform", "").lower():
            return r

    return results[0]

def is_valid_swf(data):
    """Check the SWF magic bytes: FWS (uncompressed) or CWS (zlib) or ZWS (lzma)."""
    if not data or len(data) < 8:
        return False
    return data[:3] in (b"FWS", b"CWS", b"ZWS")

def try_direct(url):
    """Attempt to download the .swf directly from its original URL."""
    if not url or url.startswith("http://localflash"):
        return None
    data = http_get(url, timeout=15)
    if is_valid_swf(data):
        return data
    return None

def try_wayback(url):
    """Look up the URL in Wayback Machine CDX, then download the raw binary."""
    if not url or url.startswith("http://localflash"):
        return None

    cdx_params = urllib.parse.urlencode({
        "url": url,
        "output": "json",
        "limit": "1",
        "fl": "timestamp,statuscode",
        "filter": "statuscode:200",
    })
    cdx_data = http_get(f"{WAYBACK_CDX}?{cdx_params}")
    if not cdx_data:
        return None

    try:
        rows = json.loads(cdx_data)
    except Exception:
        return None

    # rows[0] is the header, rows[1] is the first result
    if len(rows) < 2:
        return None

    timestamp = rows[1][0]
    raw_url = WAYBACK_RAW.format(timestamp=timestamp, url=url)
    data = http_get(raw_url, timeout=30)
    if is_valid_swf(data):
        return data
    return None

def fetch_game(slug):
    title = slug_to_title(slug)
    out_path = os.path.join(GAMES_DIR, f"{slug}.swf")

    if os.path.exists(out_path):
        size = os.path.getsize(out_path) // 1024
        print(f"  [{slug}] Already downloaded ({size} KB), skipping.")
        return "skip"

    print(f"  [{slug}] Searching Flashpoint for: {title}")
    match = search_flashpoint(title)

    if not match:
        print(f"           Not found on Flashpoint.")
        return "fail"

    found_title = match.get("title", "?")
    platform    = match.get("platform", "?")
    launch_cmd  = match.get("launchCommand", "")
    print(f"           Match: \"{found_title}\" ({platform})")
    print(f"           URL:   {launch_cmd}")

    # Step 1: Try original URL directly
    print(f"           Trying direct download...", end=" ", flush=True)
    swf = try_direct(launch_cmd)
    if swf:
        print(f"OK ({len(swf) // 1024} KB)")
    else:
        print(f"failed.")

    # Step 2: Wayback Machine fallback
    if not swf:
        print(f"           Trying Wayback Machine...", end=" ", flush=True)
        swf = try_wayback(launch_cmd)
        if swf:
            print(f"OK ({len(swf) // 1024} KB)")
        else:
            print(f"failed.")

    if swf:
        os.makedirs(GAMES_DIR, exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(swf)
        print(f"           Saved → games/{slug}.swf")
        return "ok"
    else:
        print(f"           Could not retrieve .swf.")
        return "fail"

def main():
    os.makedirs(GAMES_DIR, exist_ok=True)

    results = {"ok": [], "skip": [], "fail": []}

    print(f"app13.info game fetcher — {len(GAMES)} games\n")

    for i, slug in enumerate(GAMES, 1):
        print(f"[{i}/{len(GAMES)}]")
        outcome = fetch_game(slug)
        results[outcome].append(slug)
        print()
        if outcome != "skip":
            time.sleep(1.0)  # be polite to both APIs

    print("=" * 50)
    print(f"Done.")
    print(f"  Downloaded:  {len(results['ok'])}")
    print(f"  Skipped:     {len(results['skip'])}")
    print(f"  Failed:      {len(results['fail'])}")

    if results["fail"]:
        print(f"\nFailed games (add .swf files manually):")
        for s in results["fail"]:
            print(f"  games/{s}.swf")

if __name__ == "__main__":
    main()
