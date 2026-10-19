# Data Dictionary

Reference for every external dataset FloodScout ingests, and for the database
fields each one populates. One section per dataset.

---

## 1. Kenya county boundaries (ADM1)

Populates `geodata.Region` — the spatial backbone every downstream feature
joins against.

### Source

| | |
|---|---|
| **Dataset** | geoBoundaries `gbOpen`, Kenya, ADM1 ("Counties") |
| **Publisher** | geoBoundaries Project, William & Mary geoLab |
| **Upstream source** | RCMRD GeoPortal / Africa GeoPortal |
| **Boundary ID** | `KEN-ADM1-32016919` |
| **Year represented** | 2020 |
| **Release build date** | 12 December 2023 |
| **Licence** | **Public Domain** (geoBoundaries `gbOpen` release; upstream RCMRD GeoPortal) |
| **Attribution** | Runfola D. et al. (2020) *geoBoundaries: A global database of political administrative boundaries.* PLoS ONE 15(4): e0231866 |
| **Unit count** | 47 — matches the 47 counties created by the Constitution of Kenya (2010), First Schedule |

### Why this source

Three candidates were checked:

- **geoBoundaries via HDX / the geoBoundaries API** — chosen. It is the only
  one of the three with a **stable, machine-readable API** that resolves to a
  **commit-pinned download URL**, which means `seed_regions` is reproducible:
  the same command run next year loads byte-identical geometry. Its API also
  publishes `admUnitCount`, which independently confirmed 47 units before any
  code was written. Public Domain licensing imposes no redistribution
  constraints on a university project.
- **OCHA / IEBC Common Operational Dataset on HDX** — authoritative for
  Kenyan administrative reporting and arguably the more "official" source,
  but distributed as a zipped shapefile behind an HDX download page rather
  than a stable direct URL, which makes unattended re-download fragile.
  Retained as a documented fallback: `seed_regions` recognises its
  `ADM1_EN` / `COUNTY_NAM` attribute names, so it can be loaded via `--path`
  without a code change.
- **openAFRICA** — mirrors of the above with inconsistent vintages and
  patchier licence metadata. Not used.

### Retrieval

The command downloads this automatically; the steps below are for doing it by
hand or for verifying what the command fetched.

1. Resolve the current release (returns JSON including `gjDownloadURL` and
   `admUnitCount`):

   ```bash
   curl -s https://www.geoboundaries.org/api/current/gbOpen/KEN/ADM1/
   ```

2. Download the GeoJSON. `seed_regions` pins this to upstream commit
   `9469f09` rather than tracking `current`, so the dataset cannot shift
   underneath the project mid-semester:

   ```bash
   curl -sL -o data/raw/geoBoundaries-KEN-ADM1.geojson \
     https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/KEN/ADM1/geoBoundaries-KEN-ADM1.geojson
   ```

The file lands in `data/raw/`, which is **gitignored by design** — it is
~7.9 MB and is reproducible from the URL above, so it is never committed.

### Source schema

Attribute names were read off the real file, not assumed:

| Attribute | Example | Used? |
|---|---|---|
| `shapeName` | `Kisumu` | **Yes** → `Region.county_name` |
| `shapeISO` | `KE-42` | No (available for future joins) |
| `shapeID` | `32016919B72266624462344` | No |
| `shapeGroup` | `KEN` | No |
| `shapeType` | `ADM1` | No |

Geometry arrives mixed: **43 Polygon + 4 MultiPolygon**. The four already-multi
counties are the coastal ones with offshore islands — Mombasa, Kwale, Kilifi
and Lamu.

### Field mapping

| `Region` field | Type | Derived from |
|---|---|---|
| `name` | `CharField(100)` | `shapeName`, whitespace-stripped. With `level`, the natural key for idempotent upserts. |
| `county_name` | `CharField(100)` | For a county, the same as `name`. For a sub-county, the county it lies in (see section 2). |
| `level` | `CharField(10)` | `county` for ADM1 rows. |
| `parent` | `ForeignKey(Region)` | Empty for counties. |
| `geom` | `MultiPolygonField(srid=4326)` | Feature geometry, stored in **EPSG:4326**. Plain Polygons are wrapped in a single-element MultiPolygon to satisfy the field type. |
| `area_size` | `FloatField` | **Square kilometres.** Computed on a *clone* transformed to **EPSG:21037**, then m² ÷ 1,000,000. |

#### On the two coordinate systems

`geom` is stored in EPSG:4326 (WGS 84) because that is what Leaflet and the
REST API consume. But 4326 is a geographic CRS measured in **degrees**, so
`geom.area` returns square degrees — a meaningless number for an area column
(Kisumu would read ≈ 0.16 rather than ≈ 2,000).

`area_size` is therefore measured on a clone reprojected to **EPSG:21037
(Arc 1960 / UTM Zone 36S)**, a projected CRS in metres appropriate for Kenya.
The clone is discarded; the stored geometry stays in 4326.

### Known naming quirk

geoBoundaries labels Tharaka-Nithi County as **`Tharaka`**. All 47 counties
are present and correct; only the label is shortened. Any code joining
`Region.county_name` to another dataset — rainfall, population, IEBC
reporting — must account for this. Left as-is here so the database faithfully
reflects the cited source rather than silently diverging from it.

### Verified output

Loaded 2026-09-20 from the pinned release: **47 rows, 47 distinct county
names, all stored as MultiPolygon in SRID 4326.**

#### Is the km² computation correct?

geoBoundaries publishes its own area statistics in the API response, which
gives an independent check on the EPSG:21037 computation:

| Statistic | Computed here | Published by geoBoundaries | Difference |
|---|---|---|---|
| Largest county (Marsabit) | 75,925.86 | 75,925.18 | 0.001% |
| Smallest county (Mombasa) | 226.34 | 226.48 | 0.064% |
| Sum of all 47 | 591,348.68 | 590,898.31 | 0.076% |

The two calculations were done independently and agree to within 0.08%, so
the projection-and-divide step is sound.

#### How do these compare to gazetted county areas?

| County | Computed km² | Gazetted km² | Difference | Large water body? |
|---|---|---|---|---|
| Wajir | 56,684.1 | 56,685.8 | **0.003%** | no |
| Isiolo | 25,333.2 | 25,336.1 | **0.011%** | no |
| Nairobi | 705.3 | 694.9 | 1.5% | no |
| Mombasa | 226.3 | 229.9 | 1.6% | coastal |
| Turkana | 69,970.3 | 68,680.3 | 1.9% | Lake Turkana |
| Vihiga | 561.9 | 531.0 | 5.8% | no |
| Kisumu | **2,686.8** | 2,085.9 land / 2,652.9 incl. water | 1.3% vs incl. water | Lake Victoria |
| Marsabit | **75,925.9** | 70,961.2 | 7.0% | Lake Turkana |

Read this carefully before quoting `area_size` in the dissertation:

- **Dry inland counties match the gazetted figures almost exactly** — Wajir
  to 0.003%, Isiolo to 0.011%. That near-perfect agreement is what rules out
  an error in the area computation itself.
- **Counties on a lake or the coast read high**, because the source polygons
  include inland water that gazetted *land* areas exclude. Kisumu is the
  clearest case: 2,686.8 km² is 29% above its 2,085.9 km² land area but
  within 1.3% of its 2,652.9 km² land-plus-water figure. Marsabit's 7%
  excess is consistent with its share of Lake Turkana.
- So `area_size` is best understood as **polygon area including inland
  water**, not gazetted land area. For flood-risk work that is arguably the
  more useful denominator, but it is not interchangeable with the land-area
  figures published by KNBS, and the two should not be mixed in one table.

The sum of all 47 counties is 591,349 km² against Kenya's ~580,367 km²
total area — about 1.9% high, consistent with the same water-inclusion
effect distributed across the lake-bordering counties.

#### Multi-part geometries

Four counties arrive as MultiPolygons, all of them coastal, and their part
counts reflect real offshore islands rather than data noise:

| County | Parts |
|---|---|
| Lamu | 67 |
| Kilifi | 24 |
| Kwale | 22 |
| Mombasa | 9 |

The other 43 arrive as single Polygons and are wrapped to one-part
MultiPolygons on load.

### Loading

```bash
# all 47 counties (downloads on first run, then uses the cached copy)
docker compose exec web python manage.py seed_regions

# a single county
docker compose exec web python manage.py seed_regions --county Kisumu

# from a local file (e.g. the OCHA/IEBC shapefile)
docker compose exec web python manage.py seed_regions --path data/raw/ken_admbnda_adm1.shp

# force a fresh download
docker compose exec web python manage.py seed_regions --refresh
```

The load is wrapped in a transaction and keyed on `(level, name)` via
`update_or_create`, so re-running is safe and never duplicates rows.

---

## 2. Kenya sub-county boundaries (ADM2)

Adds the 290 sub-counties to `geodata.Region` alongside the counties, so each
one gets its own terrain, rainfall and risk score through the same ingest and
scoring commands. The dashboard draws them when a user opens a focus region
(`geodata/focus_regions.py`).

### Source

| | |
|---|---|
| **Dataset** | geoBoundaries `gbOpen`, Kenya, ADM2 ("Sub-Counties") |
| **Upstream source** | IEBC, OCHA ROSEA (2020 boundaries) |
| **Licence** | CC BY 3.0 IGO |
| **Units** | 290 (the IEBC constituencies) |
| **Pinned download** | `https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/KEN/ADM2/geoBoundaries-KEN-ADM2.geojson` |

Same upstream commit as the ADM1 file, so the two are from one release.

### Parent county

The ADM2 file does not record which county a sub-county belongs to. Each
sub-county is assigned to the county whose geometry covers the largest share
of it. The ADM1 (RCMRD) and ADM2 (IEBC) boundaries were drawn by different
agencies and do not line up exactly, so every sub-county overlaps a sliver of
its neighbours. That is why the loader takes the largest overlap rather than
testing containment. A best match below 50% is reported as a warning. On the
pinned release none is: all 290 were assigned cleanly, with Nairobi's 17
under Nairobi.

| `Region` field | Sub-county value |
|---|---|
| `name` | `shapeName`, e.g. `Kibra` |
| `county_name` | the parent county's `name`, e.g. `Nairobi` |
| `level` | `sub_county` |
| `parent` | the parent county's row |

### Known naming quirks

The ADM2 names are unique across Kenya. Some differ from current usage:
Nairobi's Dagoretti North and South appear as **`Kilimani`** and
**`Dagoretti`**, and **`Cental Imenti`** is misspelt in the source. As with
`Tharaka`, they are stored as the source spells them.

### Loading

Counties must be loaded first.

```bash
docker compose run --rm web python manage.py seed_regions --level sub_county

# one county's sub-counties
docker compose run --rm web python manage.py seed_regions --level sub_county --county Nairobi
```

Then `seed_terrain --level sub_county`, `seed_rainfall --level sub_county`
and `score_regions`. Open-Meteo rate-limits the free tier per minute and
counts each coordinate as a call. The client waits out HTTP 429 responses
and retries, so a full sub-county run takes a few minutes.

---

## 3. Model training data

What the flood model in `riskengine/ml/` is trained on. `build_training_set`
downloads all of it, caches it under `data/` (gitignored) and writes
`data/processed/training_set.csv`: one row per county per month, January
1985 to September 2021.

### Flood events (the label)

| | |
|---|---|
| **Dataset** | Global Active Archive of Large Flood Events |
| **Publisher** | Dartmouth Flood Observatory (DFO), University of Colorado |
| **Coverage** | Worldwide, 1985 to October 2021; 5,130 events, 84 reaching Kenya |
| **Download** | `https://floodobservatory.colorado.edu/temp/FloodArchive.xlsx` |
| **Pinned copy** | Wayback Machine snapshot of 15 April 2024, used when the observatory's server is down (it usually is) |
| **Citation** | Brakenridge, G.R. *Global Active Archive of Large Flood Events.* Dartmouth Flood Observatory, University of Colorado, USA |

Fields used: `Began`, `Ended`, `long`, `lat` (centroid), `Area` (km²),
`Severity` (1 large, 1.5 very large, 2 extreme). `Dead`, `Displaced` and
`MainCause` are read but not used by the model.

The archive gives each flood a centroid and an area, not an outline. A flood
is placed as a circle of that area around the centroid, and a county-month
is labelled `flood = 1` when a circle touches the county and the event
overlaps the 30 days ending on the last day of the month. This is the main
weakness of the labels: see `riskengine/ml/README.md`.

### Historical rainfall

| | |
|---|---|
| **Dataset** | Daily `precipitation_sum`, mm: ERA5 reanalysis (`models=era5`) for every date |
| **Publisher** | ECMWF / Copernicus Climate Change Service, served by Open-Meteo |
| **Endpoint** | `https://archive-api.open-meteo.com/v1/archive` |
| **Licence** | CC BY 4.0 |
| **Sampled at** | Each county's representative point, the same one `seed_terrain` uses |

Reanalysis is a model constrained by observations, not a rain gauge. It was
chosen as one consistent record over the whole period.

**Earlier training sets were not one record.** Found on 9 October 2026:
the archive endpoint, asked for no particular model (`best_match`), serves
ERA5 before 2017 and the ECMWF IFS analysis (9 km) from 2017 on. Until
then the request named no model, so the training rows of 2017 to 2021 were
IFS rainfall. `riskengine/ml/README.md`, "The Colab retrain", has the
evidence and what it means for the final test.

The request now names `models=era5`. `build_training_set --rain-model
best_match` still builds the older kind of set. The model is written to
`training_set.meta.json` as `rain_model` and travels with the trained
model; a set without it is `best_match`. ERA5 files are cached in
`data/raw/rainfall_archive/era5/`, apart from the `best_match` files above
them, so neither is read back as the other.

### El Niño index

| | |
|---|---|
| **Dataset** | Oceanic Niño Index (ONI) |
| **Publisher** | NOAA Climate Prediction Center |
| **Download** | `https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt` |
| **Licence** | Public domain |

Three-month running mean sea-surface temperature anomaly in the Niño 3.4
region. Each row uses the value that had been published by its date (the
season centred two months earlier), so the model never sees an index it
could not have had at the time.

### Seasonal rainfall forecast (outlook only)

| | |
|---|---|
| **Dataset** | ECMWF seasonal ensemble, daily `precipitation_sum` per member |
| **Endpoint** | `https://seasonal-api.open-meteo.com/v1/seasonal` |

Read by `flood_outlook` only. Never used in training and never stored as
`EnvironmentalData`.

### Columns of `training_set.csv`

| Column | Meaning |
|---|---|
| `county`, `date` | The county and the last day of the 30-day window |
| `normal_30d` | The county's mean `rain_30d` for that calendar month, 1985–2021 |
| `rain_30d` … `slope` | The model inputs, defined in `riskengine/ml/features.py` |
| `flood` | 1 if a DFO event was placed on the county in the window. The training target |
| `severity` | Highest DFO severity class among those events, else 0 |
| `events` | How many DFO events were placed on the county in the window |

---

## 4. Drainage: rivers, flood zones and built-up area

Fills three fields of `geodata.GeospatialFeature` for all 337 regions.
Loaded by `seed_drainage`, which downloads and caches everything under
`data/external/drainage/` (gitignored). Run `seed_terrain` first.

These are stored and served by the API but are **not model inputs**. The
reason is in `riskengine/ml/README.md` under "Known limits".

| Field | Meaning | Source |
|---|---|---|
| `distance_to_river` | Metres from the region's representative point to the nearest river | OpenStreetMap waterways for Kenya, HOT export on HDX, `waterway = river` only. ODbL |
| `flood_zone_fraction` | Share of the region inside the 100-year river flood extent, 0 to 1 | Copernicus Emergency Management Service, *Global river flood hazard maps* v2.1, 100-year return period, 3 arc seconds (about 90 m). CC BY 4.0 |
| `built_up_fraction` | Share of the region covered by buildings, 0 to 1 | JRC Global Human Settlement Layer, `GHS-BUILT-S` R2023A, epoch 2020, 30 arc seconds. CC BY 4.0 |

### How the two shares are computed

Regions are burnt onto the flood map's 90 m grid with `gdal_rasterize`, so
each pixel carries the id of the region it is in. `flood_zone_fraction` is
the share of a region's pixels with a flood depth above zero.
`built_up_fraction` is the mean over its pixels of built-up square metres
divided by the area of the 30-arc-second cell.

### Caveats

- **The flood map only covers larger rivers.** Small urban streams are not
  modelled, so Nairobi's sub-counties read close to zero even where the
  Nairobi, Mathare and Ngong rivers flood regularly. A low value is not
  evidence of safety.
- **River distance is from one point.** In a large region it says little
  about the region as a whole.
- **OpenStreetMap coverage is uneven.** Only `waterway = river` is used
  because streams, drains and ditches are mapped far more completely in
  towns than in rural areas.
- **Built-up area is not drainage.** It shows where the ground is sealed. It
  says nothing about whether the drains there work.

### Sanity checks on the loaded values

Budalang'i (Busia), Kenya's best-known flood plain, has the highest
flood-zone share at 80.8%. Embakasi Central (Nairobi) is the most built-up
at 36.8%. Wajir East is furthest from a mapped river at 83.4 km.

The ICPAC 100-year inundation map on HDX was the first choice for the flood
zone, but its server could not be reached on the day; the Copernicus map
replaced it.

---

## 5. UNOSAT satellite flood extents (validation only)

Used by `validate_unosat` to check the model against floods it never saw.
Never used in training. Downloaded by hand into `data/external/unosat/`
(gitignored) on 5 October 2026 and listed for the command in
`riskengine/ml/unosat_events.json`.

| | |
|---|---|
| **Publisher** | United Nations Satellite Centre (UNOSAT), UNITAR |
| **Found through** | Humanitarian Data Exchange, organisation `unosat` |
| **Licence** | Creative Commons Attribution Share-Alike (CC BY-SA), as stated on HDX |
| **Sensor** | NOAA-20 / Suomi NPP VIIRS, 375 m, cumulative over five days |

| Activation | File | Layers used | Scored for |
|---|---|---|---|
| FL20231124KEN, floods of November 2023 | `https://unosat.org/static/unosat_filesystem/3761/FL20231124KEN_gdb.zip` (5.9 MB) | `VIIRS_20231119_20231123_MaximumFloodWaterExtent_KEN` and `VIIRS_20231124_20231128_MaximumFloodWaterExtent_KEN`, merged | 30 days ending 28 November 2023 |
| FL20240426KEN, floods of April and May 2024 | `https://unosat.org/static/unosat_filesystem/3839/FL20240426KEN_gdb.zip` (12.4 MB) | `VIIRS_20240424_20240428_MaximumFloodWaterExtent_Kenya` | 30 days ending 28 April 2024 |

Two more files were downloaded and not used: product 3828 (the same
national April 2024 layer, in an earlier, smaller geodatabase) and
FL20230321KEN (Dagahaley refugee camp, March 2023, a few square kilometres).

Unzip each archive into a folder named `<activation>_<product number>`, for
example `data/external/unosat/FL20231124KEN_3761/`.

### Caveats

- **Flood water only.** UNOSAT publishes permanent water as its own layer
  and leaves it out of the flood extent. The overlap between the two is
  about 100 km² in either event.
- **Lake shores.** Water beyond the reference shoreline of Lake Turkana and
  Lake Baringo counts as flood, although those lakes have been high for
  years. It makes Turkana, Marsabit and Baringo "flooded" in both events.
- **Cloud.** Each geodatabase has a `CloudObstruction` layer. It is not
  used: a county hidden by cloud is counted as dry.
- **Resolution.** At 375 m the sensor misses narrow river and urban
  flooding. The high-resolution layers in the same files (Garissa, Dadaab,
  Nairobi and Kiambu, the Tana and Galana rivers) cover parts of counties
  only and are not used.

---

## 6. Global Flood Database (label check only)

Used by `check_labels_gfd` to measure the circle labels of section 3 against
flood water mapped from satellite. Never used in training or in scoring.
Read through Google Earth Engine; the per-county areas are cached in
`data/external/gfd/` (gitignored) and the result is kept in
`riskengine/ml/artifacts/gfd_label_check.csv`.

| | |
|---|---|
| **Dataset** | Global Flood Database v1, MODIS events |
| **Earth Engine id** | `GLOBAL_FLOOD_DB/MODIS_EVENTS/V1` |
| **Publisher** | Cloud to Street and the Dartmouth Flood Observatory |
| **Coverage** | 913 floods worldwide, 2000 to 2018, each carrying its DFO event id |
| **Sensor** | MODIS (Terra and Aqua), 250 m |
| **Licence** | CC BY-NC 4.0 |
| **Citation** | Tellman, B. et al. (2021). Satellite imaging reveals increased proportion of population exposed to floods. *Nature*, 596, 80 to 86 |

Bands used: `flooded` (1 where water was mapped during the event) and
`jrc_perm_water` (1 where the JRC Global Surface Water layer has permanent
water). Flood water is `flooded` and not `jrc_perm_water`. A county's area
of flood water is summed at the 250 m pixel.

### Caveats

- **A minority of events.** It maps 17 of the 62 floods the training set
  places on Kenya from 2000 to 2018, and not the largest ones.
- **What MODIS misses.** Water under cloud, under tree cover and in narrow
  or urban channels. "No water mapped" does not mean "no flood".
- **Non-commercial licence.** Fine for this project; it would matter if the
  system were ever offered commercially.
