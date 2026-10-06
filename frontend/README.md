# FloodScout dashboard (Client Tier)

A read-only Leaflet map of current flood risk across Kenya's 47 counties and,
in five flood-prone regions, their sub-counties. No login, no writes — it renders what the API returns and nothing else.

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
python manage.py seed_regions                    # 47 counties
python manage.py seed_regions --level sub_county # 290 sub-counties, for the region views
python manage.py score_regions                   # a RiskScore per county and sub-county
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

> `VITE_API_BASE_URL` must also match `WEB_PORT` in the project's root `.env`,
> which is the host port docker-compose publishes the API on. These drift apart
> easily. The symptom is the banner *"Could not reach
> http://localhost:PORT/api/regions/. Is the API running?"* — it names the port
> it tried, so compare that against `docker compose ps`.

## What it draws

Two views, chosen from the region bar under the header. The choice is kept in
the URL hash (`#/coast`), so a region survives a reload and the back button
leaves it.

**All Kenya** makes three requests on load, in parallel:

- `GET /regions/`: the 47 counties as a GeoJSON `FeatureCollection`,
  rendered by `L.geoJSON` as-is.
- `GET /risk-scores/latest-by-region/?level=county`: the latest score for
  every county in **one** request.
- `GET /focus-regions/`: the flood-prone regions (Coast, Lake Basin, Rift
  Valley, North Eastern, Nairobi) and the county ids in each.

**A region** (Coast, Nairobi, ...) adds two requests the first time it is
opened, then caches them:

- `GET /regions/?level=sub_county&focus_region=<slug>`
- `GET /risk-scores/latest-by-region/?level=sub_county&focus_region=<slug>`

Each sub-county is coloured by its own score. The rest of Kenya stays on
screen as a faint grey backdrop, and county borders are drawn over the
sub-counties when the region spans more than one county. The side panel adds:

- **Relocation.** Sub-counties at *very high* risk are listed under "Relocate
  now" and drawn with a dashed outline. Those at *high* risk are listed under
  "Prepare to move". The rule is `riskengine/advisory.py`, applied
  server-side to every score as `relocation_advisory`.
- **Nearby lower-risk sub-counties.** Selecting a sub-county with an advisory
  calls `GET /risk-scores/lower-risk-neighbours/?region=<id>`. That returns the
  adjacent sub-counties with a lower score, safest first, including any
  across a county line.

**Today or a month ahead.** A second bar under the region bar switches either
view between today's scores and the seasonal outlook for a coming month. It
lists the months from `GET /risk-outlook/months/` and is hidden when there are
none. Picking a month swaps the scores request for
`GET /risk-outlook/?month=<YYYY-MM-01>` with the same `level` and
`focus_region` params. Outlook rows have the same shape as scores, so the map,
legend and table draw them unchanged. The header says the map is an outlook,
the detail panel shows the chance of a large flood with its range across the
forecast ensemble, and the relocation panel and dashed outlines are hidden: an
outlook is a chance months ahead, not grounds to tell anyone to move.

Regions are joined to scores on the Feature's `id` (the `Region` pk).

There is no basemap. The polygons are the whole map, and the view is locked to
the country or region on screen.

### Colour

A traffic-light scheme: green (low), orange (moderate), light red (high) and
dark red (very high), in `src/risk.js`. The dark-theme set keeps the same hues
and lifts them enough to stand out against the dark surface.

Colour is never the only channel. The legend, the detail panel and the table
each name the category in text, and the table gives the whole dataset without
reference to the map. That matters more with this scheme, because green and
red are the hardest pair for red-green colour-blind readers. The "relocate
now" areas also carry a dashed outline. Regions with no score render in a
grey that belongs to neither end of the scale, so "unknown" cannot be misread
as "low".

### Provenance

The banner naming the scorer is built from the `model_version` values actually
present in the response, never hardcoded, and it says plainly when those values
look like a placeholder. When the trained model lands and starts stamping a real
version, the banner changes on its own.

## Known limitation

`GET /regions/` (the counties) is ~3.4 MB and takes several seconds on a cold load. Coordinates
are already trimmed to 5 decimal places (~1.1 m), which halved it; the remaining
cost is vertex count in the geoBoundaries source. Serving a simplified geometry
for map use — `ST_Simplify` behind a `?simplify=` parameter, with the full
geometry kept for analysis — is the obvious next step and is deliberately not
done here.
