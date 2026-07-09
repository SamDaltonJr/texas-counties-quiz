#!/usr/bin/env python3
"""Regenerate the baked city list in index.html (CITIES).

Fetches Texas place=city/town nodes with population from OpenStreetMap,
assigns each to the county whose polygon contains it, keeps only the
PER_COUNTY largest cities in each county (so metro counties don't crowd
the map), tiers the survivors by population (t1 major metros -> t3 towns),
sorts by population (so the renderer's label de-collision keeps the most
important labels), and injects a compact CITIES array into index.html.

Cities are stored as real lat/lon and projected at runtime by the page's
proj() (same affine as the counties and highways). County assignment is
done here in projected SVG space against the county polygons in DATA.

Usage:  python tools/build_cities.py            # fetch + rebuild
        python tools/build_cities.py --offline   # reuse cached osm_cities.json

Tiers reveal progressively as the Explore map is zoomed in.
"""
import json, re, sys, os, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.path.join(ROOT, "index.html")
CACHE = os.path.join(HERE, "osm_cities.json")

PER_COUNTY = 3   # keep only the N largest cities per county (above MIN)
# population thresholds -> tier ; below MIN a county still keeps its single
# largest place as tier 4 (revealed only when zoomed in close on that county)
T1 = 250000   # major metros: always labelled
T2 = 60000    # mid cities: revealed at moderate zoom
MIN = 5000    # towns (tier 3): revealed at deep zoom

# rank places lacking a usable population tag so a county's "largest" is stable
PLACE_RANK = {"city": 3, "town": 2, "village": 1}

OVERPASS = "https://overpass-api.de/api/interpreter"
QUERY = ('[out:json][timeout:90];'
         'area["ISO3166-2"="US-TX"][admin_level=4]->.tx;'
         '(node["place"="city"](area.tx);node["place"="town"](area.tx);'
         'node["place"="village"](area.tx););'
         'out;')


def proj(lat, lon):
    return (58.8787 * lon - 0.06978 * lat + 6304.949,
            0.220117 * lon - 69.1134 * lat + 2563.247)


def load_counties():
    html = open(INDEX, encoding="utf-8").read()
    blob = html.split("const DATA = ")[1]
    blob = blob[:blob.index("};") + 1]
    data = json.loads(blob)
    counties = []
    for co in data["counties"]:
        rings = []
        for seg in co["d"].split("M"):
            pairs = re.findall(r"(-?\d+\.?\d*),(-?\d+\.?\d*)", seg)
            if len(pairs) >= 3:
                rings.append([(float(a), float(b)) for a, b in pairs])
        counties.append({"fips": co["fips"], "cx": co["cx"], "cy": co["cy"],
                         "rings": rings})
    return counties


def in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]; xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def county_of(px, py, counties):
    for c in counties:
        for ring in c["rings"]:
            if in_ring(px, py, ring):
                return c["fips"]
    # point fell in a boundary/coastline gap: attach to nearest county centroid
    return min(counties, key=lambda c: (c["cx"] - px) ** 2 + (c["cy"] - py) ** 2)["fips"]


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
    counties = load_counties()
    # collect every place, assign it to its county
    by_county = {}
    for e in osm["elements"]:
        t = e.get("tags", {})
        name = t.get("name")
        if not name or "lat" not in e:
            continue
        rank = PLACE_RANK.get(t.get("place"), 0)
        px, py = proj(e["lat"], e["lon"])
        fips = county_of(px, py, counties)
        by_county.setdefault(fips, {})
        # dedupe by name within the county, keeping the strongest node
        cur = by_county[fips].get(name)
        cand = (population(e), rank, e["lat"], e["lon"])
        if cur is None or cand[:2] > cur[:2]:
            by_county[fips][name] = cand

    # per county: the PER_COUNTY largest places at/above MIN; if none qualify,
    # fall back to the county's single largest place (tier 4) so zooming in
    # close always surfaces at least one city regardless of the population floor
    kept = []  # (pop, name, lat, lon, tier)
    for fips, places in by_county.items():
        ranked = sorted(([pop, name, lat, lon]
                         for name, (pop, rank, lat, lon) in places.items()),
                        key=lambda r: -r[0])
        above = [r for r in ranked if r[0] >= MIN][:PER_COUNTY]
        if above:
            for pop, name, lat, lon in above:
                kept.append((pop, name, lat, lon,
                             1 if pop >= T1 else 2 if pop >= T2 else 3))
        elif ranked:
            # largest by population, breaking ties by place rank
            best = max(places.items(), key=lambda kv: (kv[1][0], kv[1][1]))
            pop, rank, lat, lon = best[1]
            kept.append((pop, best[0], lat, lon, 4))

    kept.sort(key=lambda r: (r[4], -r[0]))  # tier asc, then population desc
    rows, counts = [], {1: 0, 2: 0, 3: 0, 4: 0}
    for pop, name, lat, lon, tier in kept:
        counts[tier] += 1
        rows.append('{n:%s,lat:%.4f,lon:%.4f,t:%d}' % (json.dumps(name), lat, lon, tier))
    return "const CITIES=[" + ",".join(rows) + "];", len(rows), counts, len(by_county)


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
    js, n, counts, ncounties = build_array(osm)
    inject(js)
    print("Rebuilt CITIES: %d cities across %d counties (<=%d each above MIN; "
          "t1=%d t2=%d t3=%d t4=%d county-largest fallback), %d bytes"
          % (n, ncounties, PER_COUNTY, counts[1], counts[2], counts[3],
             counts[4], len(js)))


if __name__ == "__main__":
    main()
