# Configuration Reference

All settings live in a single file: `config/serl_mock.yaml`.

---

## Global settings

```yaml
n_households: 100   # Number of synthetic households to generate
seed: 42            # Master random seed — same seed → identical output
edition: "08"       # Appended to all output filenames as _edition<N>
```

---

## Time period

```yaml
start_year: 2019
end_year:   2019
```

One monthly HH CSV is written for every month in `[start_year, end_year]` inclusive.  Setting `end_year: 2023` produces 5 × 12 = 60 files.

---

## Household traits

Controls appliance ownership and meter traits that affect generated fields.

```yaml
household_traits:
  pv_fraction: 0.15   # Share of households with PV
  hp_fraction: 0.07   # Share of households with a heat pump
  ev_fraction: 0.10   # Share of households with an EV
  gas_meter_fraction: 0.85    # Share of households with a gas meter
  export_meter_fraction: 0.15 # Share of households with an electricity export meter
  solar_thermal_fraction: 0.02 # Share of households with solar thermal (solar water heating)
```

- Only selected export-meter households get non-zero `Elec_act_exp_hh_Wh` and `Elec_react_exp_hh_varh`.
- Non-export-meter households keep export at zero.
- `gas_meter_fraction` is what actually gates gas generation in the smart-meter and rt-summary outputs (via `has_gas_meter` in `household_traits.csv`) — see the note on `profiles.gas_fraction` below, which is a separate, currently-unused setting.
- Import and gas generation are unchanged by PV assignment.
- The exporter list file (`Elec_<year>_list_of_exporter_puprns_editionXX.csv`) uses the same PV selection (it lists households with `has_pv = 1`, not a separate export-only sample).
- `hp_fraction` drives heat-pump survey trait fields (`A1607` in the SERL survey when present, and the `B2_5_yes` heating-type field in the follow-up survey).
- `ev_fraction` drives EV survey fields (`C5` / `C6` in the SERL survey and `B3_4_yes` / `D5` / `D6` in the follow-up survey).
- `solar_thermal_fraction` drives `solarWaterHeatingFlag` in the EPC data and the `A12_Taps_SWH` / `A12_Shower_SWH` solar-water-heating fields in the SERL survey.

---

## Household profiles

Controls the **population-level** consumption parameters.  Each household draws its own values from these distributions at initialisation.

```yaml
profiles:
  base_elec_mean_wh: 175    # Mean baseline electricity per HH period (Wh)
  base_elec_std_wh:   50    # Std of per-household baseline electricity
  base_gas_mean_wh: 1500    # Mean baseline gas at peak heating demand (Wh)
  base_gas_std_wh:   300    # Std of per-household baseline gas
  gas_fraction:     0.85    # Fraction of households with a gas meter (0–1)
```

> To shift the whole population to higher consumption, increase `base_elec_mean_wh` or `base_gas_mean_wh`.  
> To make households more similar to each other, decrease the `_std` values.

> **Note:** `gas_fraction` here only sets the (currently unused) `has_gas` field on each `HouseholdProfile`. The gas meter gate actually applied when generating smart-meter and rt-summary data is `household_traits.gas_meter_fraction` above. Keep the two in sync if you rely on gas presence being consistent with the rest of the pipeline.

---

## Generation patterns

Controls the **shape** of seasonal and intraday curves and random noise behaviour.

```yaml
patterns:
  elec_seasonal_amplitude:      0.3    # ±30% swing around annual electricity mean
  gas_seasonal_amplitude:       2.0    # Winter gas heating multiplier (0 in summer)
  elec_spike_probability:      0.02    # Per-reading probability of an appliance spike
  elec_spike_max_wh:         2000.0    # Maximum extra Wh added by a spike
  summer_hot_water_probability: 0.15   # Probability of non-zero gas outside heating season
  gas_heating_threshold:        0.1    # gas_seasonal value below which "summer" rules apply
```

> To produce flatter consumption (no seasonal swing), set `elec_seasonal_amplitude: 0` and `gas_seasonal_amplitude: 0`.  
> To remove appliance spikes, set `elec_spike_probability: 0`.  
> To model an all-electric population, set `gas_fraction: 0`.

---

## Filenames (optional)

Contextual output filenames default to SERL Edition 08 naming.  They can be overridden via the `filenames:` key if needed:

```yaml
filenames:
  epc:              serl_epc_data
  survey:           serl_survey_data
  covid19_survey:   serl_covid19_survey_data
  summary:          serl_participant_summary
  followup_survey:  serl_2023_follow_up_survey_data
  exporters_prefix: Elec
```

Any other keys under `filenames:` (e.g. `followup_prefix`, `tariff_data`) are currently ignored — the tariff-data placeholder name is fixed to `serl_tariff_data_edition<edition>.csv` in `scripts/generate_mock_data.py`.

## Year for the exporter list

```yaml
year: 2023   # Not set by default — the exporter-list generator falls back to 2023
```

The contextual generator uses this top-level `year` (default `2023`, independent of `start_year`/`end_year`) only to name the exporter list file: `mock_internal/Elec_<year>_list_of_exporter_puprns_edition<edition>.csv`. Set it explicitly if `start_year`/`end_year` cover a different period, otherwise the exporter file will keep the `2023` label regardless of the smart-meter data's actual year.

---

## Adding a new setting

1. Add the key and a default value to `serl_mock.yaml`.
2. Read it in the relevant `__init__` method with `cfg.get("your_key", default)`.
3. Document it here.

---

## Weather settings

Controls the ERA5 download and CSV conversion performed in the weather step.

```yaml
weather:
  dataset:      "reanalysis-era5-single-levels"  # CDS dataset identifier
  product_type: "reanalysis"
  output_format: "netcdf"   # "netcdf" (.nc) or "grib"

  # ERA5 variables — aligned with the SERL climate data schema
  variables:
    - "2m_temperature"
    - "surface_solar_radiation_downwards"
    - "total_precipitation"
    - "10m_u_component_of_wind"
    - "10m_v_component_of_wind"

  # UK bounding box [north, west, south, east] in decimal degrees
  area: [60.0, -8.0, 49.0, 2.0]

  # Spatial resolution [lat_step, lon_step] in degrees
  grid: [0.25, 0.25]

  # Hourly download (ERA5 native resolution)
  time_step_hours: 1

  # Override date range (defaults to top-level start_year / end_year)
  # start_year: 2019
  # end_year:   2019

  # Override output directory (defaults to data/mock/serl_climate_data_edition08/)
  # output_dir: "data/mock/serl_climate_data_edition08"
```

> **CDS credentials required.** Register at https://cds.climate.copernicus.eu and add `~/.cdsapirc`:
> ```
> url: https://cds.climate.copernicus.eu/api
> key: <your-api-key>
> ```
> Alternatively set the `CDSAPI_URL` and `CDSAPI_KEY` environment variables.
> Use `--skip-weather` to bypass the download step if credentials are unavailable — the pipeline also auto-detects a missing/invalid CDS client and falls back to an empty `serl_climate_data_edition08/` folder with a warning instead of failing.

### Idempotent execution

- If a `.nc` file already exists for a month, it is **not re-downloaded**.
- If a `.csv` file already exists for a month, it is **not re-converted**.
- Re-running the pipeline is safe; only missing files are produced.
