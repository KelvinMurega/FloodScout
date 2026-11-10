# Susceptibility model: pre-registered protocol

Status: **Fixed from the merge of this file.** After that, nothing in it
changes before the run. A change after that is a new protocol version with
its own date, and `experiments.md` records why.

Version: 1. Written 29 October 2026 and committed 1 November 2026, before
any susceptibility data was sampled or any model trained.

## Question

Given that a large flood happens in a county, which places inside that
county go under water? Formally, P(pixel flooded | an event floods its
county). The hazard model already answers *when* (see `README.md`). Within a
county it ranks sub-counties at ROC AUC 0.461, no better than chance
(`artifacts/unosat_validation_sub_county.csv`). This model is judged only on
the *where*.

## 1. Unit of analysis and sample

- **Pixel**: the 250 m grid of the Global Flood Database
  (`GLOBAL_FLOOD_DB/MODIS_EVENTS/V1`, MODIS). Every feature is sampled onto
  that grid.
- **Events**: every GFD event that intersects Kenya and has at least 200
  flooded pixels (12.5 km²) inside Kenya once permanent water is removed.
  The rule is fixed here; the list of events it gives is recorded by the
  sampler, not chosen by hand.
- **Area sampled for an event**: the counties in which that event has at
  least one flooded pixel. Pixels outside those counties are not used,
  because the question assumes the county floods.
- **Permanent water** is removed from the labels and from scoring: JRC Global
  Surface Water `occurrence` of 90% or more, or the GFD band `jrc_perm_water`.
- **Flooded pixel**: GFD `flooded` = 1, not permanent water.
- **Dry pixel**: GFD `flooded` = 0, not permanent water, and GFD
  `clear_perc` of 0.5 or more, so that a pixel hidden by cloud for most of the
  event is not counted as dry. Pixels with `clear_perc` under 0.5 are dropped.
  The band is a fraction from 0 to 1, despite its name.
- **Sample size per event**: all flooded pixels up to 3,000, drawn at random
  if there are more, and 3,000 dry pixels drawn at random. Every pixel keeps
  its **sampling weight** (pixels of its class in the event area, divided by
  pixels of its class drawn), so the event's true flooded share can be
  restored.
- **Seed** for all sampling: 20261029.

## 2. Features

Fixed list. No feature is added or removed after the run starts.

| Feature | Definition |
|---|---|
| `hand` | MERIT Hydro `hnd`, metres |
| `twi` | ln(upa_m2 / tan(slope)), with the slope from MERIT `elv`, floored at 0.1 degrees |
| `log_upa` | log10(1 + MERIT `upa` in km²) |
| `dist_river` | Distance in metres to the nearest pixel with MERIT `upa` of 100 km² or more |
| `elev` | MERIT `elv`, metres |
| `slope` | Degrees, from MERIT `elv` |
| `flood_zone_100y` | 1 inside the JRC 100-year flood extent, else 0 |

Land cover (ESA WorldCover) and soil texture (SoilGrids) were candidates. Both
were cut on 1 November 2026, before the run, under refocus section 7, because
time is short. Adding them later is a new protocol version.

## 3. Splits

- **Development**: the GFD sample only.
  - **Event CV (primary)**: leave one event out. Every pixel gets an
    out-of-fold prediction from a model that never saw its event.
  - **Block CV (guard)**: 0.5 degree blocks (about 55 km), five folds,
    `GroupKFold` on the block id. Neighbouring pixels are near-copies, so a
    random split would flatter the model.
- **Test**: UNOSAT FL20231124KEN (product 3761) and FL20240426KEN (product
  3839), as listed in `unosat_events.json`. Used **once**, after the model is
  chosen (section 7). Never trained or tuned on.
- **Not used**: UNOSAT FL20230321KEN (product 3547) is kept back as a spare
  lockbox. AI4G Sentinel-1 labels are not used in version 1.

## 4. Metrics

All metrics are computed from out-of-fold predictions, using the sampling
weights.

- **Primary: within-county ROC AUC.** For each event and county with at
  least 20 flooded and 20 dry sampled pixels, the weighted ROC AUC of the
  score. The primary figure is the unweighted mean over those
  county-events. The mean weighted by flooded pixels is reported too.
- **Within-county average precision**, by the same rule. The flooded share
  differs between county-events, so this is reported next to each
  county-event's own base rate.
- **Calibration** of P(pixel flooded | event in county): Brier score, and a
  reliability plot of 10 equal-count bins on the weighted predictions.
- **Per event**: every metric is reported for every event, as well as
  pooled.
- **Block CV**: the same primary metric under block CV.

## 5. Baselines

A baseline has no fitted parameters. It is scored in exactly the same way as
the model.

| Baseline | Score |
|---|---|
| HAND | minus `hand` (lower HAND is more susceptible) |
| 100-year flood zone | `flood_zone_100y` (ties broken as ROC AUC does) |
| Distance to river | minus `dist_river` |
| The current engine | 0.461 within-county ROC AUC on UNOSAT sub-counties. This figure is only comparable at sub-county level on the test (section 7) |

The **best baseline** is the one with the highest primary metric on event
CV.

## 6. Candidates

| Model | Grid |
|---|---|
| Logistic regression (standardised inputs, L2) | `C` in {0.01, 0.1, 1, 10} |
| `HistGradientBoostingClassifier` | `learning_rate` in {0.05, 0.1} x `max_leaf_nodes` in {15, 31}; `max_iter` 300, `min_samples_leaf` 100, no early stopping |

Both use the sampling weights as sample weights. Eight configurations in
all. The configuration with the highest primary metric on event CV is
selected. Each one is trained with seeds 0 to 4, and the spread is
reported.

Because one of eight configurations is picked on the same folds, its score
is slightly flattered. The baselines are not. The margin in section 7
allows for that.

## 7. Decision rule

1. **Ship the model** if both of these hold:
   - its primary metric on event CV is at least the best baseline's
     plus **0.03**;
   - its within-county ROC AUC on block CV is not lower than the best
     baseline's on block CV.
2. **Otherwise, ship the best baseline** as the susceptibility layer. Its
   score is mapped to a probability by a one-variable logistic fit on the
   development sample. That changes the scale, not the order.
3. **Sign check before shipping**: partial dependence must fall with `hand`
   and `dist_river`, and rise with `twi`. If a sign is the wrong way round,
   stop and investigate before shipping. Finding a reason does not reopen
   the decision rule.

Both outcomes are acceptable, and whichever one happens is reported.

## 8. Test (one look)

After the decision, the shipped layer and every baseline are scored once on
the two UNOSAT events:

- **Pixel level**: the VIIRS extents are resampled to the 250 m grid. Flooded
  means a pixel is at least half covered by the extent. Dry means every
  other non-permanent-water pixel in a county with mapped water. UNOSAT has
  no cloud band, so dry is weaker evidence here than in development, and
  that is stated with the result. Same metrics as section 4, without
  sampling weights.
- **Sub-county level, against the 0.461**: the same 43 county-events and the
  same label as `validate_unosat --level sub_county` (5 km² or more of
  mapped water). Each sub-county is scored by **expected flooded area**,
  which is the sum over its pixels of susceptibility x pixel area, with
  permanent water excluded. The label is an area threshold, so a larger
  sub-county is more likely to flood whatever its terrain. For that reason
  sub-county area alone is scored as one more baseline at this level.

The result goes into `experiments.md`, the model card and the report,
whatever it is.

## 9. Look counter

| Data | Allowed looks | Used |
|---|---|---|
| UNOSAT 2023-11 and 2024-04, susceptibility | Once per shipped version | 1 (9 Nov 2026, run `20261102T1246Z_77a8329`) |
| UNOSAT 2023-03 (spare) | 0 until a protocol version names it | 0 |
| Hazard 2017 to 2021 test | **Closed** | About 7, never again |

Every look is recorded in `experiments.md` with its date, the git commit
and the result.
