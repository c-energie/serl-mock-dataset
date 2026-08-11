# Project Structure

```
serl-mock/
│
├── config/
│   └── serl_mock.yaml              # Single configuration file for the whole pipeline
│
├── data/
│   ├── reference/                  # Tracked input files (data dictionaries, BST dates, bank holidays)
│   └── mock/                       # All generated output lands here (gitignored)
│       ├── bst_dates_to_2030.csv
│       ├── serl_survey_data_dictionary_edition08.csv
│       ├── serl_covid19_survey_data_dictionary_edition08.csv
│       ├── serl_tariff_data_edition08.csv                                              # placeholder
│       ├── serl_energy_use_in_GB_domestic_buildings_2021_aggregated_statistics_edition07.csv  # placeholder
│       ├── serl_epc_data_edition08.csv
│       ├── serl_survey_data_edition08.csv
│       ├── serl_covid19_survey_data_edition08.csv
│       ├── serl_participant_summary_edition08.csv
│       ├── serl_2023_follow_up_survey_data_edition08.csv
│       ├── serl_smart_meter_rt_summary_edition08.csv
│       ├── serl_smart_meter_hh_edition08/
│       │   ├── serl_half_hourly_2019_01_edition08.csv
│       │   └── ...
│       ├── serl_smart_meter_daily_edition08/
│       │   ├── serl_smart_meter_daily_2019_edition08.csv
│       │   └── ...
│       ├── serl_climate_data_edition08/
│       │   ├── serl_climate_data_2019_01_edition08.nc   # raw ERA5 download
│       │   ├── serl_climate_data_2019_01_edition08.csv  # SERL-format CSV
│       │   └── ...
│       ├── serl_aggregated_data/                        # placeholder folder mirroring the TRE layout
│       └── mock_internal/
│           ├── puprn_master.csv
│           ├── household_traits.csv
│           └── Elec_2023_list_of_exporter_puprns_edition08.csv
│
├── docs/
│   ├── 00_overview.md              # What the project does and quick-start
│   ├── 01_structure.md             # This file
│   ├── 02_configuration.md         # serl_mock.yaml reference
│   ├── 03_generation_model.md      # How smart-meter values are generated
│   ├── 04_metadata.md              # SERL dataset column reference
│   ├── 05_epc_reference.md         # EPC quirks and region differences (E&W vs Scotland)
│   └── 06_working_notes.md         # Working notes and TODOs
│
├── notebooks/
│   └── explore_mock_data.ipynb     # Example notebook for exploring generated output
│
├── scripts/
│   ├── generate_mock_data.py       # Entry point: runs the full pipeline
│   └── generate_bank_holidays_csv.py  # One-off: fetches UK bank holidays from gov.uk
│
├── src/
│   └── serl_mock/                  # Core library package
│       ├── __init__.py
│       ├── paths.py
│       ├── ids.py
│       ├── utils.py
│       ├── profiles.py
│       ├── patterns.py
│       ├── generator_household_traits.py
│       ├── generator_smartmeter.py
│       ├── generator_contextual_data.py
│       └── weather_downloader.py
│
├── pyproject.toml
└── README.md
```

---

## Module descriptions

### `scripts/generate_mock_data.py`
The **entry point** for the full generation pipeline.  Run with `uv run python scripts/generate_mock_data.py`.  Accepts `--skip-weather` to bypass the ERA5 download step, and `--survey-only` to regenerate only the contextual datasets (step 5) from a previous full run.

The pipeline runs these steps in order:

| Step | What it does |
|---|---|
| 0 | Copies `bst_dates_to_2030.csv` as-is, and copies the survey and COVID-19 survey data dictionaries into `data/mock/` with the edition suffix applied |
| 0b | Creates empty placeholder files for datasets not yet generated (`serl_tariff_data_editionXX.csv`, the aggregated-statistics CSV) |
| 1 | Generates PUPRNs → `mock_internal/puprn_master.csv`; assigns household traits (PV, HP, EV, solar thermal, gas meter, export meter) → `mock_internal/household_traits.csv` |
| 2 | Generates monthly half-hourly smart-meter CSVs → `serl_smart_meter_hh_edition08/` |
| 3 | Generates yearly daily smart-meter CSVs → `serl_smart_meter_daily_edition08/` |
| 3b | Generates read-type data quality summary → `serl_smart_meter_rt_summary_edition08.csv` |
| 4 | Downloads ERA5 weather data via CDS API and converts to CSV → `serl_climate_data_edition08/` (skipped with `--skip-weather`; falls back to a warning and an empty output folder if CDS credentials are unavailable) |
| 5 | Generates contextual datasets (EPC, SERL survey, COVID-19 survey, participant summary, follow-up survey, exporter list) |

Skipping steps 1–4 with `--survey-only` requires `mock_internal/puprn_master.csv` and `mock_internal/household_traits.csv` to already exist from a prior full run.

### `scripts/generate_bank_holidays_csv.py`
A one-off utility that fetches the official UK bank holidays JSON from gov.uk and writes `data/reference/uk_bank_holidays_england_wales_scotland.csv`.  Run this locally if the reference file needs updating; the output is committed to the repo.

### `src/serl_mock/paths.py`
Defines `Path` constants for `CONFIG_DIR`, `DATA_DIR`, `MOCK_DIR`, `MOCK_HH_DIR`, `MOCK_DAILY_DIR`, `MOCK_CLIMATE_DIR`, `MOCK_INTERNAL_DIR`, `MOCK_AGGREGATED_DIR`, and `REFERENCE_DIR` relative to the project root.  Import these instead of hard-coding paths anywhere else.

### `src/serl_mock/ids.py`
Utilities for PUPRN identifiers:
- `make_alphanumeric_ids_ordered` — generates a deterministic list of unique IDs
- `load_puprn_list_csv` / `write_puprn_list_csv` — read/write the master CSV
- `select_household_subset` — deterministically selects a fraction of households

### `src/serl_mock/utils.py`
Shared helpers:
- `read_config` — loads a YAML or JSON config file
- `seed_random` — seeds both Python `random` and NumPy RNGs
- `write_csv` — thin wrapper around `DataFrame.to_csv`
- `with_edition_suffix` — builds Edition-stamped filenames
- `read_survey_dictionary` — loads variable names from the SERL survey data dictionary

### `src/serl_mock/profiles.py`
Defines **per-household consumption parameters**.  Each PUPRN is assigned a `HouseholdProfile` (baseline electricity Wh, baseline gas Wh, noise scale) drawn once at initialisation.  Adjusting `profiles:` in `serl_mock.yaml` shifts the population without touching any code.  See [03_generation_model.md](03_generation_model.md).

### `src/serl_mock/patterns.py`
Pure NumPy functions that return **time-based multiplier arrays**:
- `elec_seasonal_mult` / `gas_seasonal_mult` — cosine-based seasonal curves
- `elec_daily_mult` / `gas_daily_mult` — intraday demand profiles

Replacing any function here changes the shape of the generated time series without touching the generator.  See [03_generation_model.md](03_generation_model.md).

### `src/serl_mock/generator_household_traits.py`
`generate_household_traits` assigns device ownership and meter availability to each PUPRN once, writing `mock_internal/household_traits.csv`.  All downstream generators (smart-meter, contextual data) read from this file so traits are perfectly consistent across outputs.

Traits assigned: `has_pv`, `has_hp`, `has_ev`, `has_solar_thermal`, `has_gas_meter`, `has_export_meter` (all 0/1).

### `src/serl_mock/generator_smartmeter.py`
Contains three generators:

- `HHSmartMeterGenerator` — produces Edition 08-aligned half-hourly CSVs; reads household traits from `mock_internal/household_traits.csv`
- `DailySmartMeterGenerator` — aggregates HH output to daily totals
- `ReadTypeDataQualitySummaryGenerator` — builds the read-type data quality summary

`HHSmartMeterGenerator`:
- Reads configuration and instantiates household profiles at `__init__` time
- `generate_month(year, month)` builds the full T × H DataFrame vectorised
- Applies Edition 08 timestamp rules (UTC cut-off, BST/GMT labels, HH index, `Valid_read_time`)
- Computes Edition 08-style error flags with `np.where` (no Python loops)

### `src/serl_mock/generator_contextual_data.py`
`SERLContextualVariablesGenerator` produces the contextual CSV files: EPC, SERL survey, COVID-19 survey, participant summary, follow-up survey, and the exporter PUPRN list.  It reads household traits from `mock_internal/household_traits.csv` to ensure device-ownership fields (PV, HP, EV, solar thermal) are consistent with the smart-meter outputs, and shares a single England & Wales / Scotland nation assignment between the EPC and participant-summary generators so `epcVersion` and `Region` never contradict each other.

- EPC records generated field-by-field using category lists and numeric ranges, with nation-specific value vocabularies for fields that differ between England & Wales and Scotland (see [05_epc_reference.md](05_epc_reference.md))
- SERL survey, COVID-19 survey, and follow-up survey data are all driven by their respective SERL data dictionaries in `data/reference/`
- Participant summary `grid_cell` values are sampled from cells already present in the downloaded climate CSVs when available, falling back to a geometric assignment over the configured weather bounding box otherwise
- `write_all(outfolder, mock_only_outfolder)` writes all contextual files in one call; the exporter list is written to `mock_only_outfolder` (`mock_internal/`)

### `src/serl_mock/weather_downloader.py`
`WeatherDownloader` retrieves and converts ERA5 reanalysis data:
- `download_month(year, month)` — downloads a single month from the CDS API; skips if the NetCDF file already exists
- `convert_month_to_csv(year, month)` — converts the NetCDF to a SERL-format hourly CSV; skips if the CSV already exists
- `ensure_month(year, month)` — combines both steps in one call
- `ensure_all()` — runs `ensure_month` for every month in the configured date range
