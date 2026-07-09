#!/usr/bin/env python3
"""Regenerate the baked city list in index.html (CITIES).

Fetches Texas place=city/town nodes with population from OpenStreetMap,
tiers them by population (t1 major metros -> t3 towns), sorts by population
(so the renderer's label de-collision keeps the most important labels), and
injects a compact CITIES array into index.html.

Cities are stored as real lat/lon and projected at runtime by the page's
proj() (same affine as the counties and highways).

Usage:  python tools/build_cities.py            # fetch + rebuild
        python tools/build_cities.py --offline   # reuse cached osm_cities.json

Tiers reveal progressively as the Explore map is zoomed in.
"""
import json, re, sys, os, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.path.join(ROOT, "index.html")
CACHE = os.path.join(HERE, "osm_cities.json")

# population thresholds -> tier ; anything below MIN is dropped
T1 = 250000   # major metros: always labelled
T2 = 60000    # mid cities: revealed at moderate zoom
MIN = 15000   # towns (tier 3): revealed at deep zoom

OVERPASS = "https://overpass-api.de/api/interpreter"
QUERY = ('[out:json][timeout:90];'
         'area["ISO3166-2"="US-TX"][admin_level=4]->.tx;'
         '(node["place"="city"](area.tx);node["place"="town"](area.tx););'
         'out;')


def fetch():
    data = urllib.parse.urlencode({"data": QUERY}).encode()
    req = urllib.request.Request(OVERPASS, data=data,
                                 headers={"User-Agent": "texas-county-quiz/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    open(CACHE, "wb").write(raw)
    return json.loads(raw.decode("utf-8"))


def population(e):
    p = e["tags"].get("population", "").replace(",", "").strip()
    try:
        return int(p)
    except ValueError:
        return 0


def build_array(osm):
    seen = {}
    for e in osm["elements"]:
        t = e.get("tags", {})
        name = t.get("name")
        if not name or "lat" not in e:
            continue
        pop = population(e)
        if pop < MIN:
            continue
        # dedupe by name, keep the highest-population node
        if name not in seen or pop > seen[name][0]:
            seen[name] = (pop, e["lat"], e["lon"])
    cities = sorted(seen.items(), key=lambda kv: -kv[1][0])  # by population desc
    rows = []
    for name, (pop, lat, lon) in cities:
        tier = 1 if pop >= T1 else 2 if pop >= T2 else 3
        rows.append('{n:%s,lat:%.4f,lon:%.4f,t:%d}' % (json.dumps(name), lat, lon, tier))
    counts = {1: 0, 2: 0, 3: 0}
    for name, (pop, *_ ) in cities:
        counts[1 if pop >= T1 else 2 if pop >= T2 else 3] += 1
    return "const CITIES=[" + ",".join(rows) + "];", len(rows), counts


def inject(js):
    html = open(INDEX, encoding="utf-8").read()
    new, n = re.subn(r'const CITIES\s*=\s*\[.*?\];', js, html, count=1, flags=re.S)
    assert n == 1, "CITIES array not found in index.html"
    open(INDEX, "w", encoding="utf-8", newline="").write(new)


def main():
    if "--offline" in sys.argv and os.path.exists(CACHE):
        osm = json.load(open(CACHE, encoding="utf-8"))
    else:
        print("Fetching Texas cities from OpenStreetMap...")
        osm = fetch()
    js, n, counts = build_array(osm)
    inject(js)
    print("Rebuilt CITIES: %d cities (t1=%d t2=%d t3=%d), %d bytes"
          % (n, counts[1], counts[2], counts[3], len(js)))


if __name__ == "__main__":
    main()
