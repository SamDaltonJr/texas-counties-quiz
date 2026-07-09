#!/usr/bin/env python3
"""Regenerate the baked Interstate geometry in index.html (HW_PATH).

Pipeline: fetch real Texas Interstate motorways from OpenStreetMap (Overpass),
project them into the county SVG coordinate space, merge parallel carriageways,
simplify, and inject the resulting single SVG path into index.html.

The projection is the affine fit of the county polygons' own projection
(equirectangular, <1% residual) recovered from known county centroids.

Usage:  python tools/build_highways.py          # fetch + rebuild
        python tools/build_highways.py --offline # reuse cached osm_roads.json

Requires only the Python standard library.
"""
import json, re, sys, os, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.path.join(ROOT, "index.html")
CACHE = os.path.join(HERE, "osm_roads.json")

GRID = 0.5   # px grid for merging parallel carriageways / lanes
EPS  = 0.6   # px Douglas-Peucker tolerance after stitching

OVERPASS = "https://overpass-api.de/api/interpreter"
QUERY = ('[out:json][timeout:180];'
         'area["ISO3166-2"="US-TX"][admin_level=4]->.tx;'
         'way["highway"="motorway"]["ref"~"^I "](area.tx);'
         'out geom;')


def proj(lat, lon):
    return (58.8787 * lon - 0.06978 * lat + 6304.949,
            0.220117 * lon - 69.1134 * lat + 2563.247)


def numref(r):
    m = re.match(r"^I (\d+)", r or "")
    return int(m.group(1)) if m else 999


def fetch():
    data = urllib.parse.urlencode({"data": QUERY}).encode()
    req = urllib.request.Request(OVERPASS, data=data,
                                 headers={"User-Agent": "texas-county-quiz/1.0"})
    with urllib.request.urlopen(req, timeout=240) as r:
        raw = r.read()
    open(CACHE, "wb").write(raw)
    return json.loads(raw)


def dp(pts, eps):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]; bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        dd = dx * dx + dy * dy
        idx, dmax = -1, eps
        for i in range(a + 1, b):
            px, py = pts[i]
            if dd == 0:
                dist = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / dd))
                cx, cy = ax + t * dx, ay + t * dy
                dist = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
            if dist > dmax:
                idx, dmax = i, dist
        if idx != -1:
            keep[idx] = True
            stack.append((a, idx)); stack.append((idx, b))
    return [pts[i] for i in range(len(pts)) if keep[i]]


def build_path(osm):
    # keep 2-digit mainline interstates (incl. 35E/35W/69E...), drop 3-digit loops
    ways = [w for w in osm["elements"] if numref(w["tags"].get("ref", "")) < 100]
    adj, used = {}, set()

    def edge(a, b):
        return (a, b) if a <= b else (b, a)

    def snap(x, y):
        return (round(x / GRID), round(y / GRID))

    for w in ways:
        g = w.get("geometry")
        if not g or len(g) < 2:
            continue
        prev = None
        for p in g:
            if not p:
                continue
            n = snap(*proj(p["lat"], p["lon"]))
            if prev is not None and prev != n:
                a, b = edge(prev, n)
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
            prev = n

    # stitch edges into connected polylines, consuming each once
    polylines = []
    starts = [n for n in adj if len(adj[n]) != 2] + list(adj.keys())
    for s in starts:
        for nb in list(adj[s]):
            if edge(s, nb) in used:
                continue
            line, cur, nxt = [s], s, nb
            while edge(cur, nxt) not in used:
                used.add(edge(cur, nxt))
                line.append(nxt)
                cur, nxt = nxt, None
                for cand in adj[cur]:
                    if edge(cur, cand) not in used:
                        nxt = cand; break
                if nxt is None:
                    break
            if len(line) >= 2:
                polylines.append(line)

    parts, pointcount = [], 0
    for pl in polylines:
        pts = dp([(x * GRID, y * GRID) for x, y in pl], EPS)
        pts = [(round(x, 1), round(y, 1)) for x, y in pts]
        if len(pts) < 2:
            continue
        pointcount += len(pts)
        s = "M%g,%g" % pts[0] + "".join("L%g,%g" % p for p in pts[1:])
        parts.append(s)
    return "".join(parts), len(parts), pointcount


def inject(dstr):
    html = open(INDEX, encoding="utf-8").read()
    assert '"' not in dstr and "\\" not in dstr, "unsafe chars in path"
    repl = ("// Interstate geometry from OpenStreetMap (motorways ref I-*), projected into the\n"
            "// county coordinate space, simplified & parallel carriageways merged.\n"
            "// Regenerate with: python tools/build_highways.py\n"
            'const HW_PATH="' + dstr + '";')
    new, n = re.subn(r'const HW_PATH=".*?";', repl, html, count=1, flags=re.S)
    assert n == 1, "HW_PATH declaration not found in index.html"
    open(INDEX, "w", encoding="utf-8", newline="").write(new)


def main():
    if "--offline" in sys.argv and os.path.exists(CACHE):
        osm = json.load(open(CACHE))
    else:
        print("Fetching Texas interstates from OpenStreetMap...")
        osm = fetch()
    dstr, nseg, npts = build_path(osm)
    inject(dstr)
    print("Rebuilt HW_PATH: %d subpaths, %d points, %d bytes" % (nseg, npts, len(dstr)))


if __name__ == "__main__":
    main()
