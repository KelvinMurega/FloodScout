"""The flood-prone regions the dashboard lets a user drill into.

A focus region is a named group of counties that share a flood hazard — not
an administrative unit, so it has no geometry of its own and no table. It is
drawn as the union of its counties, and opened to show their sub-counties.

The grouping and hazard notes are editorial, not model output. They say
*why* an area is worth watching; the per-sub-county risk scores say how it
looks today.

County names must match Region.name for the county level exactly (the
geoBoundaries ADM1 shapeName); `geodata.tests` checks every one against the
seeded names.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FocusRegion:
    slug: str
    name: str
    hazards: str
    counties: tuple[str, ...]


FOCUS_REGIONS: tuple[FocusRegion, ...] = (
    FocusRegion(
        slug="coast",
        name="Coast",
        hazards="Coastal flooding, storm surges and rising sea levels.",
        counties=("Tana River", "Kilifi", "Lamu", "Mombasa", "Kwale"),
    ),
    FocusRegion(
        slug="lake-basin",
        name="Lake Basin",
        hazards="Riverine floods, flash floods and landslides.",
        counties=("Kisumu", "Busia", "Siaya", "Homa Bay", "Migori"),
    ),
    FocusRegion(
        slug="rift-valley",
        name="Rift Valley",
        hazards=(
            "Severe flash floods, mudslides and displacement around lakes "
            "such as Baringo and Bogoria."
        ),
        counties=("Turkana", "Baringo", "West Pokot", "Narok"),
    ),
    FocusRegion(
        slug="north-eastern",
        name="North Eastern",
        hazards=(
            "Garissa County is flagged as facing the single highest potential "
            "impact, threatening tens of thousands of households."
        ),
        counties=("Garissa", "Wajir", "Mandera"),
    ),
    FocusRegion(
        slug="nairobi",
        name="Nairobi",
        hazards="Urban flash flooding along rivers and in dense informal settlements.",
        counties=("Nairobi",),
    ),
)

BY_SLUG = {region.slug: region for region in FOCUS_REGIONS}


def get(slug: str) -> FocusRegion | None:
    return BY_SLUG.get(slug)
