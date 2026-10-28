# Hilliard Traffic Crash Dashboard

A static, GitHub-Pages-ready dashboard that maps crashes from Hilliard Police **OH-1 Traffic Crash Reports** and runs basic reports on them (time of day, driver age, conditions, hot spots, and more). Same pattern as the utility meter map in `hilliardohio/bills`: one `index.html` plus a data file.

```
crash-dashboard/
├── index.html            the dashboard (map + filters + report builder + hot spots)
├── vendor/               Leaflet 1.9.4, leaflet.heat, Chart.js 4.4.1 (no CDN needed)
├── data/
│   ├── crashes.json      what the page loads — produced by oh1_extract.py
│   ├── crashes.csv       flat one-row-per-crash export (same data, for Excel)
│   ├── persons.csv       one row per driver / occupant (age, gender, injury, belts, …)
│   └── sample.json       420 SYNTHETIC crashes for previewing the page (?data=data/sample.json)
├── oh1_extract.py        PDF → data extractor
└── make_sample_data.py   regenerates sample.json (not needed in production)
```

## 1. Turn OH-1 PDFs into data

Requires Python 3 and `pip install pdfplumber`.

```bash
# put the crash-report PDFs from the RMS in a folder, e.g. reports/2026/
python3 oh1_extract.py reports/2026 -o data
```

Each run prints one line per PDF and writes `data/crashes.json`, `crashes.csv` and `persons.csv`.

Useful options:

| option | what it does |
|---|---|
| `--merge` | keep crashes already in `data/crashes.json` and add/replace the ones in this batch, so you can drop only the *new* PDFs in a folder and re-run |
| `--keep-report-number` | publish the local report number (e.g. `2026-00001540`) as the crash id. By default the id is a short hash of it so the public file cannot be joined back to a specific report |

### What is extracted (and what is not)

Extracted from each report: date/time, lat/long, road + cross street / house number, reference point and distance, severity, manner of collision, light, weather, road condition, surface, contour, intersection/interchange flags, work zone and school zone flags, hit/skip, private property, narrative, reported/dispatch/arrival/cleared times, roadway-closed minutes; per unit: unit type, damage, vehicle year/make/model, occupants, pre-crash action, contributing circumstance, traffic control, posted and estimated speed, sequence of events; per person: role, unit, **age, gender**, injury, EMS/facility, safety equipment, seating, air bag, ejection, OL class, distraction, alcohol/drug suspected, test status/type/value, offense charged and description.

**Never written to any output:** names, dates of birth, addresses, phone numbers, license plates, VINs, insurance company/policy, operator license numbers, citation numbers, owner and carrier names, towing company, officer names and badge numbers.

Derived fields the dashboard uses: `hour`, `day_of_week`, `month`, `year`, `unit_in_error_driver_age` / `_age_band` / `_gender` (the driver of the unit marked *Unit in Error*), `primary_contributing_circumstance`, `alcohol_suspected`, `drug_suspected`, `distracted`, `citation_issued`, `injured_count`, `fatal_count`, `pedestrian_or_bicycle`, `motorcycle`, `response_minutes`.

### If a PDF fails to parse

The extractor reads values by their position on the form, anchored to printed labels, and was built against the SSRS *CrashOH1M5* layout Hilliard PD exports. A supplement or an older form version may land values in slightly different spots; the run prints `FAILED <file>` and keeps going. Send that PDF over and the box coordinates in `oh1_extract.py` can be adjusted.

## 2. Preview locally

Browsers will not `fetch()` a JSON file from `file://`, so serve the folder:

```bash
python3 -m http.server 8000
# open http://localhost:8000/            → real data (data/crashes.json)
# open http://localhost:8000/?data=data/sample.json   → synthetic preview
```

## 3. Publish on GitHub Pages

1. Create a repo under the `hilliardohio` account (e.g. `crashes`) and upload everything except `make_sample_data.py` (and `data/sample.json`, once real data exists).
2. Settings → Pages → *Deploy from a branch* → `main` / root.
3. The page is live at `https://hilliardohio.github.io/crashes/`.

Updating: re-run the extractor and commit the new `data/crashes.json` (the CSVs are optional but handy for Excel users). Nothing in `index.html` needs to change.

## Using the dashboard

* **Filters** (date range, severity, light, weather, road condition, manner, at-fault driver age, day of week, road search, and flag chips for alcohol, drugs, distracted, in-intersection, ped/bike, motorcycle, cited, hit/skip) apply to everything on the page at once.
* **Map** — markers colored by severity, sized by persons injured; click for the crash details and narrative. *Heat map* toggles a density layer; *Fit to results* re-zooms as filters change. Clicking a row in the crash list or the hot-spot table flies the map there.
* **Report builder** — choose a field to count crashes by (hour of day, day of week, month, year, driver age band, gender, light, weather, road condition, manner, contributing circumstance, unit type, road, road + cross street, …) and optionally split it by a second field (severity, gender, light, weather, alcohol, intersection, year, day of week, age band). Download the table as CSV.
* **Hot spots** — top 25 road / cross-street combinations with injury, fatal and alcohol counts.
* **Crashes in view** — sortable list of the filtered crashes with CSV download.

The page respects the viewer's light/dark system setting.
