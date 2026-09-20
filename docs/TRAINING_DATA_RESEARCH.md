# FloodScout — Training Data Research

This documents where FloodScout's model inputs and, critically, its
**training labels** (historical flood occurrence) should come from, for the
Kisumu County / Lower Nyando pilot area named in the Sprint 1 plan. Sprint 1
already scoped the predictor (feature) datasets in detail; this file focuses
on filling the gap Sprint 1 flagged as "not API-accessible" — historical
flood ground truth — and gives a validated, published methodology for
generating it, plus a compiled event catalogue to target it against.

## 1. The core problem: predictors are easy, labels are hard

Elevation, rainfall, land cover, and soil data are all available from open
APIs (Section 3 recaps these). What a supervised flood risk model actually
needs beyond that is: **for specific places and dates, did it flood or not**
— and that is not sitting in any single API. It has to be built, and the
good news is that a peer-reviewed 2026 study did exactly this for exactly
this pilot area.

## 2. Recommended approach: replicate the Auma et al. (2026) method

**Auma, H., Gebreslasie, M., & Osio, A. (2026). "Flood risk modeling for
Kisumu County, Kenya: integrating multi-source geospatial data and machine
learning." *Frontiers in Environmental Science*, 14.**
https://www.frontiersin.org/journals/environmental-science/articles/10.3389/fenvs.2026.1829199/full

This is the same Kisumu-focused study the Sprint 1 plan already cited as
justification for the pilot area — it is now published, and it lays out a
complete, reproducible label-generation pipeline that FloodScout's
`geodata/services/earth_engine.py` is already positioned to replicate,
since it already pulls SRTM and CHIRPS via the same Earth Engine project.

**Their method, step by step:**

1. Pull **Sentinel-1 GRD** (`COPERNICUS/S1_GRD`, C-band SAR, 10 m) image
   pairs from Earth Engine: one "pre-flood" baseline image and one
   "flood-phase" image for each known rainy-season window, 2014–2025.
2. Run **change detection** between the two images (backscatter drop over
   water is a strong, weather-independent signal SAR is good at).
3. Apply **Otsu's method** for automatic threshold selection on the
   backscatter-difference image to separate flooded from non-flooded
   pixels — this removes the need to hand-pick a threshold per scene.
4. Mask out **permanent water** using the JRC Global Surface Water
   Occurrence dataset (`JRC/GSW1_3/Global Surface Water`, >50% occurrence
   threshold) so Lake Victoria itself isn't counted as "flood."
5. **QA every candidate flood label by hand** against Kenya Meteorological
   Department records and local news reports — this is the step that
   makes the labels trustworthy rather than pure algorithm output.
6. Chip the result into **64×64 pixel patches**, keeping ones that are
   roughly 70–90% flood pixels. Their final set: 240 patches, split
   192 (2014–2022) train / 48 (2023–2025) validation.

This is directly implementable as an extension of the existing
`geodata/services/earth_engine.py` module (add `get_sentinel1_flood_extent`
and `get_surface_water_mask` alongside the existing
`get_srtm_elevation`/`get_chirps_rainfall` functions) — all four datasets
it needs (`S1_GRD`, `GSW1_3`, plus SRTM/CHIRPS already present) are
available in the same Earth Engine project already registered
(`floodscout-166914`). It also matches the System Architecture and
Activity Diagram's "extract features → calculate score" pipeline shape,
just with an added offline labeling step before Sprint 3 training begins.

**What it does not fully specify:** the exact meteorological dataset used
for temperature/rainfall/wind features, and the Lake Victoria shoreline
shapefile source — both worth a clarifying look at the paper's supplementary
material or a direct email to the authors if the published version doesn't
resolve it, since FloodScout's own CHIRPS pipeline can likely substitute
for the rainfall half regardless.

## 3. Predictor (feature) datasets — Sprint 1's list, still current

No changes needed here; re-confirmed as still live and correct:

| Layer | Source | Notes |
|---|---|---|
| SRTM 30 m DEM | `USGS/SRTMGL1_003` (Earth Engine) | elevation, slope, TWI |
| ESA WorldCover 10 m | `ESA/WorldCover/v200` | land cover / built-up |
| CHIRPS daily rainfall | `UCSB-CHG/CHIRPS/DAILY` | already in `earth_engine.py` |
| JRC Global Surface Water | `JRC/GSW1_3/GlobalSurfaceWater` | now also needed for label masking, not just visualization |
| HydroRIVERS / HydroSHEDS | hydrosheds.org | distance-to-river |
| ISRIC SoilGrids | rest.isric.org | soil properties |
| OpenStreetMap | via `osmnx` | roads, buildings, waterways |

## 4. Ready-made flood-extent products (supplement / cross-check)

These don't need Sentinel-1 processing from scratch and are worth checking
before committing to fully manual labeling:

- **Global Flood Monitoring (GFM)** — Copernicus Emergency Management
  Service, run by TU Wien/EODC/DLR/LIST. Fully automatic, near-real-time
  Sentinel-1-based flood extent, freely accessible.
  https://www.tuwien.at/en/mg/geo/rs/open-data/gfm ·
  https://extwiki.eodc.eu/GFM/PUM/Introduction · access is via WMS/WCS or
  the openEO platform (https://docs.openeo.cloud/usecases/gfm/) — the
  exact historical-coverage start date and a Kenya-specific access
  walkthrough weren't in the pages fetched during this research; worth a
  follow-up look at the Product User Manual before relying on it, but it's
  a real candidate for either replacing or QA-checking the manual Otsu
  pipeline above, since it runs the same kind of algorithm at global scale.
- **UNOSAT Live Web Map — Floods in Kenya**, on HDX:
  https://data.humdata.org/dataset/unosat-live-web-map-flood-in-kenya —
  UNOSAT's own product page (https://unosat.org/products/3829) is a
  JS-rendered app that couldn't be read directly during this research; the
  HDX dataset page is the more reliable entry point.
- **Dartmouth Flood Observatory — Global Active Archive of Large Flood
  Events** (1985–present, polygon extents for large events worldwide):
  https://data.humdata.org/dataset/global-active-archive-of-large-flood-events-dfo
  · also mirrored as a Google Earth Engine asset
  (`GLOBAL_FLOOD_DB/MODIS_EVENTS/V1`,
  https://developers.google.com/earth-engine/datasets/catalog/GLOBAL_FLOOD_DB_MODIS_EVENTS_V1)
  — worth checking whether any of its logged events fall in the Nyando
  basin specifically; it's a large-event archive so smaller, localized
  Nyando floods may not be captured at its resolution.

## 5. Historical flood event catalogue — Kisumu / Lower Nyando

This is the concrete "when did it actually flood here" reference list
Sprint 1 said would need manual compilation. Use it two ways: (a) as the
`docs/DATA_DICTIONARY.md`-style historical-label spreadsheet Sprint 1
called for, and (b) as the target date list for pulling Sentinel-1
pre/post image pairs in Section 2's pipeline.

| Date / period | Event | Areas affected | Source |
|---|---|---|---|
| 1961 | Major regional flooding | Nyando basin (general) | [Nyando River — Wikipedia](https://en.wikipedia.org/wiki/Nyando_River) |
| 1997–1998 | El Niño flooding | Nyando basin (general) | [Nyando River — Wikipedia](https://en.wikipedia.org/wiki/Nyando_River) |
| 2020 | Lake Victoria backflow | Ahero, West Kano | [Nyando River — Wikipedia](https://en.wikipedia.org/wiki/Nyando_River) |
| 23–30 Apr 2023 | River Nyando & Maenya River overflow | Nyando Sub-County/Ahero, Kolwa Central, Kisumu West/East, Nyakach; ~2,700 households displaced county-wide by 30 Apr | [FloodList](https://floodlist.com/africa/kenya-floods-kisumu-mandera-april-2023) · [ReliefWeb — Kenya Floods Mar 2023](https://reliefweb.int/disaster/ff-2023-000051-ken) |
| Late Mar–mid-May 2024 (MAM long rains) | Nationwide flooding (Kenya–Tanzania floods); Nyando burst its banks around 6 May | Ahero, Muhoroni, Nyakach constituencies; 600+ trapped; part of a national 280,000+ displaced, 267 dead | [ACAPS briefing PDF](https://www.acaps.org/fileadmin/Data_Product/Main_media/20240514_ACAPS_Briefing_note_Kenya_Floods.pdf) · [ReliefWeb — Kenya Floods Apr 2024](https://reliefweb.int/disaster/fl-2024-000045-ken) · [Capital News](https://capitalfm.africa/thousands-displaced-as-nyando-river-bursts-submerging-homes/) · [Kenya News Agency](https://www.kenyanews.go.ke/floods-ravage-kisumu-county-as-homes-businesses-submerged/) |
| Nov 2024 | Nyando burst its banks again | Kano Plains, Ahero | [Nyando River — Wikipedia](https://en.wikipedia.org/wiki/Nyando_River) |
| 6 Mar – Apr 2026 | Nationwide flash flooding; Sondu-Miriu River (adjacent basin, also Kisumu/Nyanza) burst its banks ~24 Mar | Kisumu County (Sondu-Miriu: 381 households displaced); Nyando-specific locations flagged at-risk: Kakola, Onjiko, Kochogo, East Kano, Okanja, Kamidumbi, Panadol; national toll 112 dead, ~34,765 displaced | [2026 Kenya floods — Wikipedia](https://en.wikipedia.org/wiki/2026_Kenya_floods) · [Capital FM — flood warning](https://capitalfm.africa/kenya-flood-warning-river-nyando-tana/) · [The Star](https://www.the-star.co.ke/news/2026-03-24-flash-floods-kill-84-across-kenya) |

Recurring pattern worth noting for the methodology chapter: Nyando flooding
is strongly bimodal, clustering in the March–May long rains and, less
consistently, the October–December short rains, and is worsened by Lake
Victoria backflow at the river mouth (Ahero/West Kano) — consistent with
the "Explainer: Why Kisumu often floods" background piece:
https://nation.africa/kenya/counties/kisumu/explainer-why-kisumu-often-floods-4616562

## 6. Structured/downloadable sources for cross-checking impact figures

- **ReliefWeb Kenya disaster pages** (structured, one per event, with linked
  sitreps/maps/infographics): [Floods – Mar 2023](https://reliefweb.int/disaster/ff-2023-000051-ken),
  [Floods – Apr 2024](https://reliefweb.int/disaster/fl-2024-000045-ken)
- **Kenya Red Cross** operation pages with downloadable SitReps:
  [Floods 2024](https://redcross.or.ke/floods/) (MAM SitRep, 18 Jun 2024),
  [Floods 2026](https://redcross.or.ke/floods-2026/)
- **ACAPS Kenya Floods Briefing Note** (14 May 2024, PDF, county-by-county
  breakdown): https://www.acaps.org/fileadmin/Data_Product/Main_media/20240514_ACAPS_Briefing_note_Kenya_Floods.pdf
- **openAFRICA — Kenya flood impact matrix (MAM 2020), CSV, direct
  download**: https://open.africa/dataset/kenya-flash-floods-2020 — smaller
  and older (2020) than the pilot window, but immediately usable as-is if a
  quick CSV of quantified impact-by-county is wanted before building
  anything else.
- **Kenya National Natural Disaster Inventory** (Kenya Open Data /
  OasisHUB, shapefile, requires free login to download):
  https://oasishub.co/dataset/kenya-national-natural-disaster-inventory —
  covers multiple disaster types including floods; county-level granularity
  wasn't confirmed during this research and should be checked once
  downloaded.

## 7. What to do next (Sprint 2)

1. Extend `geodata/services/earth_engine.py` with the Section 2 pipeline
   (`S1_GRD` change detection + Otsu threshold + `GSW1_3` water mask),
   targeting the dates in Section 5's table as the first batch of
   pre/post image pairs.
2. Hand-QA the resulting candidate flood extents against Section 5's
   catalogue and the KMD bulletins it cites, exactly as Auma et al. did —
   this is the step that turns an SAR threshold into a trustworthy label.
3. Populate `docs/DATA_DICTIONARY.md` (per the Sprint 2 backlog) using
   Section 5's table as the historical-label seed, and Section 3's table
   for the predictor layers.
4. Treat GFM (Section 4) as a cross-check on the manually generated labels
   rather than a replacement, until its access mechanics and Kenya
   coverage are confirmed directly against the Product User Manual.
