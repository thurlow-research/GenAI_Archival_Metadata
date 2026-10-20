# Copyright Records

Pilot: extract structured metadata from copyright records.

- **Corpus:** Library of Congress [Motion Picture Copyright Descriptions Collection](https://www.loc.gov/collections/motion-picture-copyright-descriptions/) (~32,700 items, 1912-1930 online; Class L photoplays, Class M non-photoplays). PDFs are image-only microfilm scans.
- **Target schema:** _to be defined_
- **Ground truth:** tier 1 = the LoC record (`record.json`: title, claimant, registration no.); tier 2 = hand annotation of flagged items
- **Status:** sampling script done

## fetch_loc_copyright_sample.py

Downloads a stratified random sample (year x class) with PDFs, raw LoC records, and optional page PNGs.

```bash
python3 -m venv .venv && .venv/bin/pip install requests pypdf pypdfium2   # pypdf, pypdfium2 optional
.venv/bin/python fetch_loc_copyright_sample.py --dry-run                   # show strata and allocation
.venv/bin/python fetch_loc_copyright_sample.py --out loc_sample --n 100 --years 1917-1925 --render-png
```

Options: `--n` (100), `--years` (`1917-1925` or `1917,1920,1928`), `--class-m-share` (0.2), `--oversample` (3), `--max-pages` (10), `--annotate` (40), `--render-png`, `--dpi` (200), `--seed` (2026), `--dry-run`.

**How it samples.** Items are allocated equally across years, split L/M per year. Per stratum it draws `n x oversample` random candidates, gets each one's page count cheaply (a 4 KB Range request, max of `/Count N`), bins them (1p, 2-3p, 4-10p, 11p+, unknown) as a document-type proxy, and selects round-robin across bins. Only selected PDFs are downloaded. Output is deterministic for a given seed.

**Output** (`<out>/`): `manifest.csv`, `annotation_template.csv` (flagged items; never overwritten if present, so hand annotations are safe), `run.json`, `items/<id>/{record.json,<id>.pdf,page-NN.png}`.

**Resuming.** Rerun the same command: existing PDFs and PNGs are skipped, PDFs are written via `.part` then renamed, and page counts are cached in `.pagecount_cache.json`. If pypdf is installed, manifest page counts and bins are corrected to the real count after download.

Requests are rate limited (~3 s between API calls, ~1 s between file requests) with exponential backoff.
