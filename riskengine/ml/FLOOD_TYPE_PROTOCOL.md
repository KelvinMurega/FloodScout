# Likely flood type per place: pre-registered protocol

Status: **Draft for Kelvin's approval.** Once this file is merged, nothing in
it changes before the test. A later change is a new protocol version with its
own date, and `experiments.md` records why.

Version: 1. Written 5 November 2026, before any share in section 2 was
computed and before any flood was labelled with a type.

## Question

When a place floods, what kind of flood is it most likely to be? FloodScout
says *when* a county is at risk (the hazard model) and *where* inside it the
water goes (the susceptibility layer). It does not say *how*, and the advice
differs: a rising lake gives days of warning and needs people moved; a flash
flood gives minutes and needs people off the roads and out of gullies.

The answer is a label per sub-county, shown beside its risk and in its
alerts: "Likely flood type: river overflow (Nzoia); rising lake". It is a
description of the ground, not a forecast. It does not change any score or
alert level.

## 1. Types

| Type | What happens | Where it is expected |
|---|---|---|
| **Rising lake** | A lake rises over weeks and stays high (Baringo, Naivasha, Victoria shores) | Land close to a large lake |
| **River overflow** | A river bursts its banks after rain upstream (Tana, Nzoia, Nyando, Athi) | Floodplains of large rivers |
| **Urban drainage** | Rain cannot drain from paved, built-up ground (Nairobi estates, Mombasa) | Built-up land |
| **Flash flood** | Runoff from steep ground in hours: gullies, culverts, a dam break (Mai Mahiu, 2024) | Steep ground |

A place with no type above its line in section 3 is labelled **"No
dominant type"**: water from rain on flat, open ground.

## 2. Shares (the only inputs)

Every 250 m pixel of Kenya, on the grid of the susceptibility layer
(`SUSCEPTIBILITY_PROTOCOL.md`, section 1), is flagged four times. Permanent
water (JRC occurrence of 90% or more) is left out of every share.

| Flag | A pixel is flagged when |
|---|---|
| `near_lake` | Within 2 km of a lake of 10 km² or more: the `near_lake` band of `susceptibility/layers.py`, with the settings in `configs/susceptibility_v1.toml` |
| `floodplain` | Inside the JRC 100-year flood extent, **or** MERIT HAND of 5 m or less within 1 km of a river with 100 km² or more upstream |
| `built` | GHSL built-up surface (`JRC/GHSL/P2023A/GHS_BUILT_S`, epoch 2020) covers 20% or more of the pixel |
| `steep` | MERIT slope of 5 degrees or more |

A sub-county's **share** of a flag is its flagged pixels over all its
non-permanent-water pixels. The four shares of all 290 sub-counties are
computed once, by one Earth Engine export, and written with their SHA-256 to
`data/processed/flood_type_shares.csv`.

## 3. Rule

For each type, a sub-county **has** the type when both hold:

1. its share is in the **top fifth** (80th percentile or above) of the 290
   sub-counties, so no line is tuned by hand; and
2. its share is at least the **floor**: 0.05 for rising lake, river
   overflow and urban drainage, 0.10 for flash flood (steep ground is common
   in the highlands, and a little of it says little).

A sub-county shows **at most two types**, those with the highest percentile
ranks, the first being its **likely** type. Ties go in the order of the
table in section 1. A county shows the types of its sub-counties weighted by
area: the two with the largest flagged share over the whole county, under the
same two conditions computed over the 47 counties.

Nothing is fitted. The rule has no parameter left to choose once this file
is merged.

## 4. Test labels (written before the shares are computed)

Each label is one **place-event**: a sub-county named in a report of a
flood, with the cause the report gives. They are written to
`data/reported-floods/flood_types.csv` with the report's URL, date, the
sentence quoted, and the type, **before** section 2 is run. A label's type
comes only from the report's words:

| The report says | Type |
|---|---|
| a lake rose, backflow from a lake, a lakeshore submerged | Rising lake |
| a river burst its banks, overflowed, broke a dyke | River overflow |
| drains blocked, estates or streets flooded in a town, storm water | Urban drainage |
| flash flood, a dam or embankment burst, a culvert blocked on a slope, mudslide with water | Flash flood |

A report that gives no cause, or two causes for one place, gives no label for
that place. Sources: OCHA and Kenya Red Cross situation reports and flash
updates on ReliefWeb, and NDOC releases, 2018 to 2026. At least **40**
place-events are needed; with fewer, the result is reported as descriptive
only and the types are not shown in the app.

## 5. Metric and baseline

- **Hit**: the reported type is one of the (at most two) types the rule gives
  that sub-county.
- **Hit rate**: hits over place-events.
- **Baseline**: every sub-county gets the two most common reported types in
  the label set. Scored the same way.
- Per type: **recall** (labels of that type the rule gives) and **precision**
  (of sub-counties given that type, the share of their labels that match),
  reported for every type with 5 labels or more.
- 95% intervals by bootstrap over place-events, 2,000 draws, seed 20261105.

## 6. Decision rule

1. **Show the types** in the app if the rule's hit rate is at least the
   baseline's **plus 10 percentage points**.
2. Even then, a **type is dropped** (never shown) if it has 5 labels or more
   and its recall is under 0.5.
3. **Otherwise, do not show types.** The four shares are still facts about
   the ground and may be shown as such ("12% of this sub-county is
   floodplain"), with no type named.

Both outcomes are acceptable, and whichever one happens is reported.

## 7. What this does not claim

- It does not say a flood *will* happen; the hazard model does that.
- A sub-county is tens of kilometres across. "River overflow" means its
  floodplain is large, not that every home in it is on the floodplain.
- Reports name the places that were reached and written about, more often
  towns and lakeshores than remote floodplains. That bias is in the labels
  and is stated with the result.

## 8. Look counter

| Data | Allowed looks | Used |
|---|---|---|
| `data/reported-floods/flood_types.csv`, version 1 | Once | 0 |

The look is recorded in `experiments.md` with its date, the git commit and
the result.
