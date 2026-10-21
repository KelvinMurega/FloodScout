"""Why one region got its score, in numbers and in words.

Built on the exact split of a Random Forest prediction in
riskengine/ml/explain.py. Each factor's share is given in probability and,
like everything on the dashboard, as a multiple of the usual chance of a
large flood, so the parts read on the same scale as the score itself:

    usual chance (1.0x) + El Niño (+1.3x) - rain (-0.4x) ... = 1.6x

The words are generated from the numbers by fixed templates, so the same
score always reads the same way and the text can be tested.
"""

from __future__ import annotations

import datetime as dt

from riskengine.ml import infer as scorer
from riskengine.ml.explain import explain_row, grouped

# A factor smaller than this, in times the usual chance, is not worth a word.
NEGLIGIBLE = 0.05


def _times(probability_points: float, usual: float | None) -> float | None:
    return round(probability_points / usual, 2) if usual else None


# Labels that open with a name keep their capital in mid-sentence.
PROPER = ("El Niño", "Indian Ocean")


def _mid(label: str) -> str:
    """`label` as it reads after the start of a sentence."""
    return label if label.startswith(PROPER) else label[0].lower() + label[1:]


def _and(labels: list[str]) -> str:
    """ "A", "A and b", "A, b and c": the first as given, the rest mid-sentence."""
    rest = [_mid(label) for label in labels[1:]]
    return labels[0] if not rest else f"{', '.join([labels[0], *rest[:-1]])} and {rest[-1]}"


def _signed(times: float) -> str:
    return f"{'+' if times >= 0 else '−'}{abs(times):.1f} times the usual chance"


def summary(factors: list[dict], final_times: float | None) -> str:
    """A few sentences: what pushed the score up, what held it down, the total.

    Each sentence names at most two factors and gives the amount for exactly
    the factors it names.
    """
    up = [f for f in factors if (f["times_usual"] or 0) >= NEGLIGIBLE]
    down = [f for f in factors if (f["times_usual"] or 0) <= -NEGLIGIBLE]
    sentences = []
    if up:
        first = up[0]
        sentences.append(
            f"{first['label']} raised the risk the most ({_signed(first['times_usual'])})."
        )
        if len(up) > 1:
            named = up[1:3]
            sentences.append(
                f"{_and([f['label'] for f in named])} also raised it "
                f"({_signed(sum(f['times_usual'] for f in named))})."
            )
    if down:
        named = down[:2]
        verb = "held it down" if len(named) == 1 else "held it down the most"
        sentences.append(
            f"{_and([f['label'] for f in named])} {verb} "
            f"({_signed(sum(f['times_usual'] for f in named))})."
        )
    if not up and not down:
        sentences.append("No input moved the risk far from the usual chance.")
    if final_times is not None:
        sentences.append(f"Together: about {final_times:.1f} times the usual chance.")
    return " ".join(sentences)


def explain_score(model: dict, region, date: dt.date, usual: float | None = None) -> dict:
    """The explanation the API serves for `region` on `date`."""
    usual = scorer.USUAL_CHANCE if usual is None else usual
    terrain = region.geospatial_features.order_by("-pk").first()
    row = scorer.region_row(model, region, scorer.region_rain(region, date), date, terrain)
    explanation = explain_row(model, row)

    factors = [
        {
            "key": factor["key"],
            "label": factor["label"],
            "contribution": round(factor["contribution"], 4),
            "times_usual": _times(factor["contribution"], usual),
        }
        for factor in grouped(explanation)
    ]
    final_times = _times(explanation.probability, usual)
    # Which part of the score the rain that has fallen accounts for: the
    # difference between "it rained a lot" and "it is that time of year".
    rain_part = sum(f["contribution"] for f in factors if f["key"] in ("rain", "ground"))
    return {
        "probability": round(explanation.probability, 4),
        "times_usual": final_times,
        "baseline": round(explanation.bias, 4),
        "baseline_times_usual": _times(explanation.bias, usual),
        "factors": factors,
        "rain_share": round(rain_part, 4),
        "driven_by": "rain" if rain_part >= NEGLIGIBLE * (usual or 0.1) else "conditions",
        "summary": summary(factors, final_times),
    }
