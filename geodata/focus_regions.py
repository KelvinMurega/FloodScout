"""The flood-prone regions the dashboard lets a user drill into.

A focus region is a named group of counties that share a flood hazard — not
an administrative unit, so it has no geometry of its own and no table. It is
drawn as the union of its counties, and opened to show their sub-counties.

The model is trained on all 47 counties (the flood archive is national, and
fewer counties means fewer rows to learn from); these regions are where the
dashboard presents it. They start from the government's own list and add
four counties the evidence below says it leaves out:

- The 18 counties the Interior Ministry and the National Disaster Operations
  Centre (NDOC) put on high alert for the 2026 El Niño, announced 10 August
  2026, grouped as Coast, Lake Basin, Rift Valley and North Eastern, with
  Nairobi named as an urban centre at risk.
- Nakuru: the Mai Mahiu flash flood of 29 April 2024 killed at least 45
  people (OCHA Kenya Flash Update No. 3, 29 April 2024).
- Isiolo, Kajiado, Machakos and Kiambu: flood water mapped by UNOSAT from
  satellite in November 2023 and April 2024, summed per county in
  riskengine/ml/artifacts/unosat_validation.csv (Isiolo 2,033 km², Kajiado
  602, Machakos 109, Kiambu 26). gfd_label_check.csv in the same folder
  shows mapped water in Kajiado, Machakos and Isiolo in earlier floods too.

The hazard notes say *why* an area is worth watching; the risk scores say
how it looks today. They are not model output.

County names must match Region.name for the county level exactly (the
geoBoundaries ADM1 shapeName); `geodata.tests` checks every one against the
seeded names.
"""

from __future__ import annotations

from dataclasses import dataclass

NDOC_2026 = (
    "Interior Ministry and NDOC, 18 counties on high alert for the 2026 El Niño, "
    "10 August 2026"
)
UNOSAT = (
    "UNOSAT flood extents FL20231124KEN and FL20240426KEN, per county in "
    "riskengine/ml/artifacts/unosat_validation.csv"
)


@dataclass(frozen=True)
class FocusRegion:
    slug: str
    name: str
    hazards: str
    counties: tuple[str, ...]
    sources: tuple[str, ...]
    # Slugs this region had before, kept so saved links still open it.
    former_slugs: tuple[str, ...] = ()


FOCUS_REGIONS: tuple[FocusRegion, ...] = (
    FocusRegion(
        slug="coast",
        name="Coast",
        hazards="Coastal flooding, storm surges and rising sea levels.",
        counties=("Tana River", "Kilifi", "Lamu", "Mombasa", "Kwale"),
        sources=(NDOC_2026,),
    ),
    FocusRegion(
        slug="lake-basin",
        name="Lake Victoria Basin",
        hazards=(
            "Riverine floods where the Nyando, Nzoia, Sondu and Kuja rivers "
            "reach the lake, flash floods and landslides."
        ),
        counties=("Kisumu", "Busia", "Siaya", "Homa Bay", "Migori"),
        sources=(NDOC_2026,),
    ),
    FocusRegion(
        slug="rift-valley",
        name="Rift Valley",
        hazards=(
            "Flash floods and mudslides, displacement around lakes such as "
            "Baringo and Bogoria, and the Mai Mahiu flood in Nakuru that "
            "killed at least 45 people on 29 April 2024."
        ),
        counties=("Turkana", "Baringo", "West Pokot", "Narok", "Nakuru"),
        sources=(NDOC_2026, "OCHA Kenya Flash Update No. 3, 29 April 2024 (Mai Mahiu)"),
    ),
    FocusRegion(
        slug="northern-rivers",
        name="Northern rivers",
        hazards=(
            "Riverine floods along the Tana around Garissa and the Ewaso "
            "Ng'iro through Isiolo; satellites mapped over 2,000 km² of flood "
            "water in Isiolo in the 2023 short rains."
        ),
        counties=("Garissa", "Wajir", "Mandera", "Isiolo"),
        sources=(NDOC_2026, UNOSAT),
        former_slugs=("north-eastern",),
    ),
    FocusRegion(
        slug="nairobi-metro",
        name="Nairobi metro",
        hazards=(
            "Urban flash floods along the Nairobi and Athi rivers: Kiambu "
            "upstream, informal settlements in the city, and Athi River, "
            "Kitengela and Mlolongo downstream in Machakos and Kajiado."
        ),
        counties=("Nairobi", "Kiambu", "Machakos", "Kajiado"),
        sources=(NDOC_2026, UNOSAT),
        former_slugs=("nairobi",),
    ),
)

BY_SLUG = {region.slug: region for region in FOCUS_REGIONS}
BY_SLUG.update(
    {former: region for region in FOCUS_REGIONS for former in region.former_slugs}
)


def get(slug: str) -> FocusRegion | None:
    """The region with this slug, or with this as a former slug."""
    return BY_SLUG.get(slug)
