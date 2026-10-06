#!/usr/bin/env python3
"""Download a stratified random sample from the Library of Congress Motion Picture
Copyright Descriptions Collection, for a GenAI structured-extraction pilot.

Strata are year x class (L = photoplays, M = non-photoplays). Candidates are
oversampled, binned by PDF page count (a proxy for document type), and the final
selection mixes bins round-robin. See README.md for details.

Requires: requests. Optional: pypdf (verifies page counts after download),
pypdfium2 (--render-png).
"""

import argparse
import csv
import datetime as dt
import json
import random
import re
import struct
import sys
import time
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import requests

try:
    import pypdf
except ImportError:
    pypdf = None
try:
    import pypdfium2
except ImportError:
    pypdfium2 = None

COLLECTION_URL = "https://www.loc.gov/collections/motion-picture-copyright-descriptions/"
PARTOF = "motion picture copyright descriptions collection: class {c}, 1912-1977"
PAGE_SIZE = 150  # API maximum
YEAR_MIN, YEAR_MAX = 1912, 1930
API_DELAY = 3.0
FILE_DELAY = 1.0
MAX_ATTEMPTS = 6
BINS = ["1p", "2-3p", "4-10p", "11p+"]  # "unknown" is used only as a last resort
TITLE_SUFFIX = re.compile(
    r"\.?\s*Motion picture copyright descriptions collection\.\s*Class [LM], 1912-1977\.?\s*$",
    re.I,
)

MANIFEST_FIELDS = [
    "id", "title", "claimant", "registration_no", "date", "class", "page_bin",
    "page_count", "annotate", "item_url", "pdf_url", "pdf_path",
]
ANNOTATION_FIELDS = [
    "id", "title", "pdf_path", "cast", "director", "writer", "source_work",
    "distributor", "reels_or_length", "release_date", "genre", "notes",
]


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- HTTP

class Fetcher:
    """requests wrapper: rate limiting, retries with exponential backoff."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "GenAI_Archival_Metadata-pilot/1.0 (research sampling; contact via GitHub "
            "thurlow-research)"
        )
        self._last = {"api": 0.0, "file": 0.0}
        self._delay = {"api": API_DELAY, "file": FILE_DELAY}

    def _wait(self, kind):
        gap = self._delay[kind] - (time.monotonic() - self._last[kind])
        if gap > 0:
            time.sleep(gap)

    def _request(self, kind, fn, what):
        """Run fn(); retry on network errors, 429/5xx, and anything fn rejects."""
        err = None
        for attempt in range(MAX_ATTEMPTS):
            self._wait(kind)
            retry_after = None
            try:
                result = fn()
                self._last[kind] = time.monotonic()
                return result
            except requests.HTTPError as e:
                err = e
                resp = e.response
                if resp is not None:
                    if resp.status_code not in (429, 500, 502, 503, 504):
                        raise
                    try:
                        retry_after = float(resp.headers.get("Retry-After", ""))
                    except ValueError:
                        pass
            except (requests.RequestException, ValueError, OSError) as e:
                # ValueError covers non-JSON bodies; OSError/ChunkedEncodingError
                # covers IncompleteRead and broken connections.
                err = e
            self._last[kind] = time.monotonic()
            backoff = retry_after or min(5 * 2 ** attempt, 120)
            log(f"  ! {what}: {type(err).__name__}: {str(err)[:100]} "
                f"(attempt {attempt + 1}/{MAX_ATTEMPTS}; retry in {backoff:.0f}s)")
            time.sleep(backoff)
        raise RuntimeError(f"giving up on {what}: {err}")

    def api_json(self, params, what="API call"):
        def go():
            r = self.session.get(COLLECTION_URL, params=params, timeout=60)
            r.raise_for_status()
            return r.json()  # ValueError if not JSON
        return self._request("api", go, what)

    def page_count(self, pdf_url):
        """Cheap page count from the first 4 KB: max of all '/Count N'. None if unknown."""
        def go():
            with self.session.get(pdf_url, headers={"Range": "bytes=0-4095"},
                                  stream=True, timeout=60) as r:
                r.raise_for_status()
                head = r.raw.read(4096, decode_content=False)  # never pull a full 200 body
            return head
        head = self._request("file", go, f"range {pdf_url}")
        counts = [int(m) for m in re.findall(rb"/Count (\d+)", head)]
        return max(counts) if counts else None

    def download(self, url, dest):
        part = dest.with_name(dest.name + ".part")

        def go():
            with self.session.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
        self._request("file", go, f"download {url}")
        part.rename(dest)


# ------------------------------------------------------------------------ sampling

def parse_years(spec):
    years = set()
    for tok in spec.split(","):
        tok = tok.strip()
        if "-" in tok:
            a, b = tok.split("-", 1)
            years.update(range(int(a), int(b) + 1))
        elif tok:
            years.add(int(tok))
    bad = [y for y in years if not YEAR_MIN <= y <= YEAR_MAX]
    if bad or not years:
        sys.exit(f"--years must be within {YEAR_MIN}-{YEAR_MAX}; got {sorted(bad) or spec!r}")
    return sorted(years)


def allocate(n, years, share, seed):
    """Equal allocation across years (remainder to randomly chosen years), L/M split per year."""
    base, rem = divmod(n, len(years))
    extra = set(random.Random(f"{seed}-alloc").sample(years, rem))
    plan = []
    for y in years:
        ny = base + (y in extra)
        m = int(ny * share + 0.5)
        plan.append({"year": y, "class": "L", "n": ny - m})
        plan.append({"year": y, "class": "M", "n": m})
    return [s for s in plan if s["n"] > 0]


def query_params(year, cls, page=1, **extra):
    p = {"fo": "json", "dates": f"{year}/{year}", "c": PAGE_SIZE, "sp": page,
         "at": "results,pagination", "fa": "partof:" + PARTOF.format(c=cls.lower())}
    p.update(extra)
    return p


def stratum_total(fetcher, year, cls):
    d = fetcher.api_json(query_params(year, cls, c=1), f"count {year} class {cls}")
    return d["pagination"]["of"]


def sample_candidates(fetcher, stratum, total, oversample, seed):
    """Random global indices -> fetch only the result pages that contain them."""
    want = min(total, stratum["n"] * oversample)
    rng = random.Random(f"{seed}-{stratum['year']}-{stratum['class']}")
    indices = sorted(rng.sample(range(total), want))
    by_page = defaultdict(list)
    for i in indices:
        by_page[i // PAGE_SIZE + 1].append(i % PAGE_SIZE)
    cands = []
    for page, offsets in sorted(by_page.items()):
        d = fetcher.api_json(query_params(stratum["year"], stratum["class"], page),
                             f"{stratum['year']} class {stratum['class']} page {page}")
        results = d.get("results", [])
        for off in offsets:
            if off < len(results):
                cands.append(results[off])
    return cands


def parse_record(rec, cls):
    item = rec.get("item", {})
    notes = item.get("notes") or []
    reg = next((m.group(1).strip() for n in notes
                if (m := re.search(r"Registration No\.:\s*(.+)", n))), "")
    claimants = [re.sub(r"\s*\(Copyright claimant\)\s*$", "", c).strip()
                 for c in item.get("contributors") or []]
    res = rec.get("resources") or [{}]
    return {
        "id": (rec.get("number") or [""])[0],
        "title": TITLE_SUFFIX.sub("", item.get("title", "")).strip(),
        "claimant": "; ".join(claimants),
        "registration_no": reg,
        "date": rec.get("date", ""),
        "class": cls,
        "item_url": rec.get("id", ""),
        "pdf_url": res[0].get("pdf", ""),
    }


def page_bin(count):
    if count is None:
        return "unknown"
    if count <= 1:
        return "1p"
    if count <= 3:
        return "2-3p"
    if count <= 10:
        return "4-10p"
    return "11p+"


def select_round_robin(cands, k, start):
    """Pick k items cycling through page bins (rotated by `start`); unknown only as filler."""
    bins = {b: [c for c in cands if c["page_bin"] == b] for b in BINS}
    order = BINS[start % len(BINS):] + BINS[:start % len(BINS)]
    chosen = []
    while len(chosen) < k and any(bins.values()):
        for b in order:
            if bins[b] and len(chosen) < k:
                chosen.append(bins[b].pop(0))
    if len(chosen) < k:
        chosen += [c for c in cands if c["page_bin"] == "unknown"][:k - len(chosen)]
    return chosen


# ----------------------------------------------------------------------- rendering

def write_png(path, width, height, rgb_rows):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    raw = b"".join(b"\x00" + row for row in rgb_rows)  # filter type 0 per scanline
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    path.write_bytes(png)


def render_pngs(pdf_path, out_dir, max_pages, dpi):
    pdf = pypdfium2.PdfDocument(str(pdf_path))
    try:
        for i in range(min(len(pdf), max_pages)):
            dest = out_dir / f"page-{i + 1:02d}.png"
            if dest.exists():
                continue
            page = pdf[i]
            bitmap = None
            try:
                bitmap = page.render(scale=dpi / 72, rev_byteorder=True)  # RGB
                w, h, stride = bitmap.width, bitmap.height, bitmap.stride
                buf = bytes(bitmap.buffer)
                rows = (buf[y * stride:y * stride + w * 3] for y in range(h))
                write_png(dest, w, h, rows)
            finally:
                if bitmap is not None:
                    bitmap.close()
                page.close()
    finally:
        pdf.close()


def verified_page_count(pdf_path):
    if pypdf is None:
        return None
    try:
        return len(pypdf.PdfReader(str(pdf_path)).pages)
    except Exception as e:  # corrupt scans happen; keep the estimate
        log(f"  ! pypdf could not read {pdf_path.name}: {e}")
        return None


# ---------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--out", default="loc_sample", help="output directory")
    ap.add_argument("--n", type=int, default=100, help="total items to select")
    ap.add_argument("--years", default="1917-1925", help='e.g. "1917-1925" or "1917,1920,1928"')
    ap.add_argument("--class-m-share", type=float, default=0.2, help="fraction of Class M per year")
    ap.add_argument("--oversample", type=int, default=3, help="candidates per selected item")
    ap.add_argument("--max-pages", type=int, default=10, help="max pages rendered per PDF")
    ap.add_argument("--annotate", type=int, default=40, help="items flagged for hand annotation")
    ap.add_argument("--render-png", action="store_true", help="render PDF pages to PNG")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--dry-run", action="store_true",
                    help="query stratum sizes and print the plan; download nothing")
    args = ap.parse_args()

    if not 0 <= args.class_m_share <= 1:
        sys.exit("--class-m-share must be between 0 and 1")
    if args.render_png and pypdfium2 is None:
        sys.exit("--render-png needs pypdfium2 (pip install pypdfium2)")

    started = dt.datetime.now(dt.timezone.utc)
    out = Path(args.out)
    years = parse_years(args.years)
    plan = allocate(args.n, years, args.class_m_share, args.seed)
    fetcher = Fetcher()

    log("Checking API ...")
    for s in plan:
        s["total"] = stratum_total(fetcher, s["year"], s["class"])
    log(f"{'year':<6}{'class':<7}{'available':>10}{'target':>8}")
    for s in plan:
        log(f"{s['year']:<6}{s['class']:<7}{s['total']:>10}{s['n']:>8}")
    if args.dry_run:
        log("Dry run: nothing downloaded.")
        return

    (out / "items").mkdir(parents=True, exist_ok=True)
    cache_path = out / ".pagecount_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    # 1. candidates -> page counts -> round-robin selection, per stratum
    selected, records = [], {}
    for si, s in enumerate(plan):
        if s["total"] == 0:
            log(f"[{s['year']} {s['class']}] no items online; skipping")
            continue
        raw = sample_candidates(fetcher, s, s["total"], args.oversample, args.seed)
        cands = []
        for rec in raw:
            c = parse_record(rec, s["class"])
            if not c["id"] or not c["pdf_url"]:
                continue
            if c["id"] not in cache:
                cache[c["id"]] = fetcher.page_count(c["pdf_url"])
                cache_path.write_text(json.dumps(cache))
            c["page_count"] = cache[c["id"]]
            c["page_bin"] = page_bin(c["page_count"])
            records[c["id"]] = rec
            cands.append(c)
        picked = select_round_robin(cands, s["n"], si)
        log(f"[{s['year']} {s['class']}] {len(cands)} candidates -> {len(picked)} selected "
            f"({dict(Counter(c['page_bin'] for c in picked))})")
        selected += picked

    # 2. choose annotation items
    ids = sorted(c["id"] for c in selected)
    flagged = set(random.Random(f"{args.seed}-annotate").sample(ids, min(args.annotate, len(ids))))

    # 3. download, render
    for n, c in enumerate(selected, 1):
        d = out / "items" / c["id"]
        d.mkdir(parents=True, exist_ok=True)
        pdf = d / f"{c['id']}.pdf"
        c["pdf_path"] = str(pdf.relative_to(out))
        c["annotate"] = "yes" if c["id"] in flagged else ""
        (d / "record.json").write_text(json.dumps(records[c["id"]], indent=2, ensure_ascii=False))
        if pdf.exists() and pdf.stat().st_size > 0:
            log(f"({n}/{len(selected)}) {c['id']}: already downloaded")
        else:
            log(f"({n}/{len(selected)}) {c['id']}: downloading ({c['page_bin']})")
            fetcher.download(c["pdf_url"], pdf)
        actual = verified_page_count(pdf)
        if actual is not None and actual != c["page_count"]:
            log(f"  note: {c['id']} page count {c['page_count']} -> {actual} (pypdf)")
            c["page_count"], c["page_bin"] = actual, page_bin(actual)
        if args.render_png:
            render_pngs(pdf, d, args.max_pages, args.dpi)

    # 4. manifests
    selected.sort(key=lambda c: (c["date"], c["class"], c["id"]))
    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, MANIFEST_FIELDS, extrasaction="ignore")
        w.writeheader()
        for c in selected:
            w.writerow({**c, "page_count": "" if c["page_count"] is None else c["page_count"]})
    ann_path = out / "annotation_template.csv"
    if ann_path.exists():
        log(f"{ann_path.name} exists; leaving it untouched (it may hold hand annotations)")
    else:
        with open(ann_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, ANNOTATION_FIELDS, extrasaction="ignore")
            w.writeheader()
            for c in selected:
                if c["annotate"]:
                    w.writerow(c)

    # 5. run.json + summary
    mix = {
        "year": dict(sorted(Counter(c["date"] for c in selected).items())),
        "class": dict(Counter(c["class"] for c in selected)),
        "page_bin": dict(Counter(c["page_bin"] for c in selected)),
    }
    (out / "run.json").write_text(json.dumps({
        "args": vars(args), "argv": sys.argv[1:], "seed": args.seed,
        "started": started.isoformat(), "finished": dt.datetime.now(dt.timezone.utc).isoformat(),
        "counts": {"selected": len(selected), "annotate": len(flagged)},
        "strata": plan, "mix": mix,
    }, indent=2))
    log(f"\nDone: {len(selected)} items in {out}/ ({len(flagged)} flagged for annotation)")
    for k, v in mix.items():
        log(f"  {k}: {v}")


if __name__ == "__main__":
    main()
