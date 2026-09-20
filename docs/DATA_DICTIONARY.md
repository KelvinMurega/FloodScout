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
| `county_name` | `CharField(100)` | `shapeName`, whitespace-stripped. Natural key for idempotent upserts. |
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

The load is wrapped in a transaction and keyed on `county_name` via
`update_or_create`, so re-running is safe and never duplicates rows.
