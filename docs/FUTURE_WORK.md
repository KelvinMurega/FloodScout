# Future work

Ideas that are out of scope for this capstone, kept here instead of being
built. Anything proposed for a frozen module (see `docs/ARCHITECTURE.md`)
lands here too.

## Engine

- **Vulnerability.** Add the third term of Risk = Hazard x Exposure x
  Vulnerability once a current, sub-county dataset exists (for example the
  KNBS poverty and housing tables from the next census).
- **Upstream routing.** Let rain over a river's upstream basin raise the
  hazard of the counties downstream (the Tana, the Nzoia, the Ewaso Ng'iro).
- **Flood depth.** Replace the "under water or not" susceptibility with a
  depth estimate from a hydraulic model.
- **A finer grid.** 0.1 degree cells need a rainfall source without
  Open-Meteo's free-tier limit, such as CHIRPS from Earth Engine.

## Machine learning operations

- **MLflow, DVC, Airflow.** Not needed at this size. The registry file, the
  snapshot manifests and the run folders do the same job and are easier to
  defend. Worth adopting if more models or more people join.
- **Automatic promotion.** Models are promoted only when Kelvin merges the
  registry PR. Automating it would need a longer record of shadow periods
  first.

## Reference clients (frozen)

- **Alerts keyed on the combined risk index.** The alert rules read
  `risk_category`, which comes from the hazard model. Once the risk index is
  validated, the rules could use it so that a sub-county alert reflects
  where the water goes, not only the county's chance. This is U-CS 13's
  decision to make.
