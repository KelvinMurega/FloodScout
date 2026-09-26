"""Query-string filters shared by every endpoint that lists regions or
per-region rows.

    ?level=county|sub_county   one administrative level
    ?parent=<id>               the sub-counties of one county
    ?focus_region=<slug>       everything inside a focus region's counties

`prefix` is the lookup path from the queryset's model to Region — empty for
Region itself, "region__" for RiskScore and friends — so one implementation
serves them all.
"""

from __future__ import annotations

from rest_framework.exceptions import ValidationError

from . import focus_regions
from .models import Region


def filter_by_region_params(queryset, params, prefix: str = "", default_level: str | None = None):
    level = params.get("level") or (None if params.get("parent") else default_level)
    if level:
        if level not in Region.Level.values:
            raise ValidationError({"level": f"Expected one of {Region.Level.values}, got {level!r}."})
        queryset = queryset.filter(**{f"{prefix}level": level})

    parent = params.get("parent")
    if parent:
        if not parent.isdigit():
            raise ValidationError({"parent": "Expected a region id."})
        queryset = queryset.filter(**{f"{prefix}parent_id": int(parent)})

    slug = params.get("focus_region")
    if slug:
        focus = focus_regions.get(slug)
        if focus is None:
            raise ValidationError({"focus_region": f"Unknown focus region {slug!r}."})
        # county_name is the enclosing county at both levels, so this one
        # lookup matches a focus region's counties and their sub-counties.
        queryset = queryset.filter(**{f"{prefix}county_name__in": focus.counties})

    return queryset
