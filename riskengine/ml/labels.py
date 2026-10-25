"""Flood labels from mapped water where a satellite saw the flood, from the circle elsewhere.

The training labels place each Dartmouth Flood Observatory flood as a circle
and call every county it touches flooded. Against the floods the Global
Flood Database mapped, two in three of those counties held no flood water,
and more counties held water outside a circle than inside one (see "The
labels against mapped flood water" in riskengine/ml/README.md).

So for a flood the database mapped, a county is labelled from the map: it
flooded if it held at least `min_km2` of mapped flood water, whether or not
the circle touched it, and it did not if the circle touched it and no water
was mapped. Every other flood keeps its circle. A county no flood touched
stays unflooded.

Each row also gets where its label came from:

  mapped  a mapped flood was in the window and its map covered the county
  circle  a flood no satellite mapped labels it flooded
  none    no flood touched it

and `weights` turns that into how much a row counts in training, so a label
the circles alone vouch for can count for less than one the map does
(MODEL_IMPROVEMENT_PLAN.md, Phase 3).
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from riskengine.ml.features import WINDOW_DAYS

MAPPED, CIRCLE, NONE = "mapped", "circle", "none"


def mapped_labels(keys, placed, events, mapped, in_window, min_km2: float = 5.0):
    """(labels, sources) for each (county, window end) in `keys`.

    `placed` is {county: [events whose circle touches it]}, `events` is
    {event id: event} for the mapped floods, `mapped` is {event id: {county:
    km² of mapped flood water}} and `in_window(event, start, end)` decides
    whether a flood falls in a window, as build_training_set does.
    """
    labels = np.zeros(len(keys), dtype=int)
    sources = np.full(len(keys), NONE, dtype=object)
    for index, (county, end) in enumerate(keys):
        start = end - dt.timedelta(days=WINDOW_DAYS - 1)
        circles = {event.id for event in placed.get(county, ())}
        from_circle = any(
            in_window(event, start, end) for event in placed.get(county, ()) if event.id not in mapped
        )
        wet = covered = False
        for event_id, water in mapped.items():
            event = events.get(event_id)
            if event is None or not in_window(event, start, end):
                continue
            here = water.get(county, 0.0) >= min_km2
            wet |= here
            # The map is evidence only where it looked: under the circle, or
            # wherever it found water.
            covered |= here or event_id in circles
        labels[index] = int(from_circle or wet)
        sources[index] = CIRCLE if from_circle else MAPPED if covered else NONE
    return labels, sources


def weights(sources, circle_weight: float) -> np.ndarray:
    """How much each row counts in training: `circle_weight` for a circle-only label, else 1."""
    return np.where(np.asarray(sources) == CIRCLE, circle_weight, 1.0)
