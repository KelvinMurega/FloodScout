# FloodScout dashboard (Client Tier)

A read-only Leaflet map of current flood risk across Kenya's 47 counties. No
login, no writes — it renders what the API returns and nothing else.

## Why Vite and not a Django template

Both work. This is a separate static app because:

- **The architecture already says so.** Chapter 4 describes four tiers, with
  the Presentation Tier served separately in development. `settings.py` already
  lists `http://localhost:5173` in `CORS_ALLOWED_ORIGINS` — the project
  anticipated a client on its own origin before this existed.
- **It keeps the API honest.** Talking to Django over HTTP from another origin
  exercises the real contract, CORS included. A template would reach into the
  ORM-backed context and quietly bypass both, so the API could rot without
  anyone noticing.
- **It can be deployed as static files.** No Python needed to serve the map.

The cost is a Node toolchain. Kept to two packages — `leaflet` and `vite` — with
no framework and no bundler config beyond pinning the port.

## Running it

The API must be running first (`docker compose up`), with the database seeded
and scored:

```bash
python manage.py seed_regions     # 47 counties
python manage.py score_regions    # a RiskScore per county for today
```

Then:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

`npm run build` emits static files to `dist/`.

## Configuring the API URL

Never hardcoded. Resolved in this order, first match wins:

| Source | Example | Use |
|---|---|---|
| `?api=` on the page URL | `http://localhost:5173/?api=http://localhost:8010/api` | point at another backend without a rebuild |
| `VITE_API_BASE_URL` | see `.env.example` | normal dev/build config |
| built-in fallback | `http://localhost:8000/api` | the docker-compose default |

Copy `.env.example` to `.env.local` to set it locally (`.env.local` is
gitignored).

> If you change the dev server's port, change `CORS_ALLOWED_ORIGINS` in
> `floodscout/settings.py` to match, or the browser will block every request.

## What it draws

Two requests on load, in parallel:

- `GET /regions/` — a GeoJSON `FeatureCollection`, rendered by
  `L.geoJSON` as-is. No geometry conversion client-side.
- `GET /risk-scores/latest-by-region/` — the latest score for every county in
  **one** request. The per-region endpoint would cost 47.

Counties are joined to scores on the Feature's `id` (the `Region` pk).

### Colour

The four risk categories are *ordinal* — reordering them changes their meaning —
so the ramp is a single hue with monotone lightness steps rather than four
unrelated colours. Both light and dark ramps were checked mechanically, not by
eye: monotone lightness, adjacent lightness gap >= 0.06, the step nearest the
surface clearing 2:1 against it, and a single hue (11 degrees of spread on
light, 7 on dark). Because lightness carries the order, the ramp survives
greyscale, printing and every form of colour-vision deficiency.

Colour is never the only channel. The legend, the detail panel and the
all-counties table each name the category in text, and the table gives the whole
dataset without reference to the map. Counties with no score render in a
desaturated grey that belongs to neither end of the ramp, so "unknown" cannot be
misread as "low".

The basemap is plain OpenStreetMap desaturated to grey by a CSS filter. A
neutral basemap is a choropleth requirement, not a style preference — on
full-colour tiles the county fills compete with roads and landcover. OSM is used
because it needs no API key; Carto's `light_all`/`dark_all` now watermark every
tile with "API KEY REQUIRED".

### Provenance

The banner naming the scorer is built from the `model_version` values actually
present in the response, never hardcoded, and it says plainly when those values
look like a placeholder. When the trained model lands and starts stamping a real
version, the banner changes on its own.

## Known limitation

`GET /regions/` is ~3.4 MB and takes several seconds on a cold load. Coordinates
are already trimmed to 5 decimal places (~1.1 m), which halved it; the remaining
cost is vertex count in the geoBoundaries source. Serving a simplified geometry
for map use — `ST_Simplify` behind a `?simplify=` parameter, with the full
geometry kept for analysis — is the obvious next step and is deliberately not
done here.
