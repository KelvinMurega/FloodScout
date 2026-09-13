"""Google Earth Engine client helpers.

Wraps `ee.Initialize` behind this project's own EE_PROJECT_ID setting (see
.env.example and the noncommercial GEE registration documented in
AI_USE_LOG.md), so the rest of the codebase never touches the `ee` package's
global state directly.

Used by the Sprint 2 data-ingestion pipelines to pull CHIRPS rainfall, SRTM
elevation and Sentinel-1 flood-extent imagery into the GeospatialFeature /
EnvironmentalData tables (Chapter 4, Section 4.2.3, DR-01/DR-02).

Requires `earthengine authenticate` to have been run once on the machine
running the ingestion code (see README.md) so a local credential already
exists on disk — this module only initializes the client, it does not
handle interactive auth.
"""
from __future__ import annotations

import logging

import ee
from django.conf import settings

logger = logging.getLogger(__name__)

_initialized = False


def initialize_earth_engine(force: bool = False) -> None:
    """Initialize the Earth Engine Python client against EE_PROJECT_ID.

    Idempotent per-process (safe to call at the top of every ingestion
    task/management command) unless `force=True`.
    """
    global _initialized
    if _initialized and not force:
        return

    project_id = getattr(settings, "EE_PROJECT_ID", "") or ""
    if not project_id:
        raise RuntimeError(
            "EE_PROJECT_ID is not set — copy .env.example to .env and fill it "
            "in with the project id from your Earth Engine registration."
        )

    ee.Initialize(project=project_id)
    _initialized = True
    logger.info("Earth Engine initialized for project %s", project_id)


def get_srtm_elevation(geometry: "ee.Geometry", scale: int = 30) -> float:
    """Mean elevation in metres over `geometry`, from the SRTM 30m DEM.

    Feeds GeospatialFeature.elevation (Chapter 4, DR-01).
    """
    initialize_earth_engine()
    dem = ee.Image("USGS/SRTMGL1_003")
    stats = dem.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=geometry, scale=scale, maxPixels=1e9
    )
    return stats.get("elevation").getInfo()


def get_chirps_rainfall(
    geometry: "ee.Geometry", start_date: str, end_date: str, scale: int = 5000
) -> float:
    """Total rainfall in mm over `geometry` between start_date and end_date
    (YYYY-MM-DD strings), from the CHIRPS daily precipitation dataset.

    Feeds EnvironmentalData.rainfall_intensity (Chapter 4, DR-01/DR-02).
    """
    initialize_earth_engine()
    collection = (
        ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
        .filterDate(start_date, end_date)
        .filterBounds(geometry)
    )
    total = collection.sum()
    stats = total.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=geometry, scale=scale, maxPixels=1e9
    )
    return stats.get("precipitation").getInfo()
