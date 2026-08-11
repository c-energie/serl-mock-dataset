<p align="center">
  <img src="docs/serl-mock_logo.png" alt="serl-mock logo" width="400"/>
</p>

# serl-mock

A lightweight Python package that generates mock datasets matching the structure
and naming conventions of the **SERL (Smart Energy Research Lab) Observatory** data Edition08 release.

Use it to build and test analysis pipelines locally, without needing access to the real data in the Data Safe Haven (DSH) or Trusted Research Environment (TRE).

---

## What is generated

Running the pipeline writes outputs under `data/mock/`. The full repository layout is:

```text
serl-mock/
├── config/
│   └── serl_mock.yaml                         # Central configuration (households, seed, edition, profiles…)
├── docs/
│   ├── 00_overview.md                         # Project overview and key concepts
│   ├── 01_structure.md                        # Module descriptions
│   ├── 02_configuration.md                    # Full configuration reference
│   ├── 03_generation_model.md                 # How smart-meter values are synthesised
│   ├── 04_metadata.md                         # SERL column reference
│   ├── 05_epc_reference.md                    # EPC quirks and region differences (E&W vs Scotland)
│   ├── 06_working_notes.md                    # Working notes and TODOs
│   └── documentation/                         # Official SERL dataset PDFs
├── notebooks/
│   └── explore_mock_data.ipynb
├── scripts/
│   ├── generate_mock_data.py                  # Main entry point — runs the full pipeline
│   └── generate_bank_holidays_csv.py
├── src/
│   └── serl_mock/
│       ├── generator_contextual_data.py       # EPC, survey, participant summary generators
│       ├── generator_household_traits.py      # Household trait assignment (PV, HP, EV…)
│       ├── generator_smartmeter.py            # Half-hourly and daily smart-meter generators
│       ├── ids.py                             # PUPRN generation and management
│       ├── paths.py                           # Output path helpers
│       ├── patterns.py                        # Seasonal and diurnal load patterns
│       ├── profiles.py                        # Per-household consumption profiles
│       ├── utils.py                           # Shared utilities
│       └── weather_downloader.py              # ERA5/CDS download and conversion
│
├── data/mock/                                 # Generated output (created at runtime)
│   ├── bst_dates_to_2030.csv
│   ├── serl_survey_data_dictionary_edition08.csv
│   ├── serl_covid19_survey_data_dictionary_edition08.csv
│   ├── serl_tariff_data_edition08.csv
│   ├── serl_energy_use_in_GB_domestic_buildings_2021_aggregated_statistics_edition07.csv
│   ├── serl_epc_data_edition08.csv
│   ├── serl_survey_data_edition08.csv
│   ├── serl_covid19_survey_data_edition08.csv
│   ├── serl_participant_summary_edition08.csv
│   ├── serl_2023_follow_up_survey_data_edition08.csv
│   ├── serl_smart_meter_rt_summary_edition08.csv
│   ├── serl_smart_meter_hh_edition08/
│   │   └── serl_half_hourly_<YYYY>_<MM>_edition08.csv
│   ├── serl_smart_meter_daily_edition08/
│   │   └── serl_smart_meter_daily_<YYYY>_edition08.csv
│   ├── serl_climate_data_edition08/           # Populated only when weather download is enabled
│   │   ├── serl_climate_data_<YYYY>_<MM>_edition08.nc
│   │   └── serl_climate_data_<YYYY>_<MM>_edition08.csv
│   ├── serl_aggregated_data/                  # placeholder
│   └── mock_internal/
│       ├── puprn_master.csv
│       ├── household_traits.csv
│       └── Elec_2023_list_of_exporter_puprns_edition08.csv
└── pyproject.toml
```

Notes:

- `serl_smart_meter_rt_summary_edition08.csv` is the read-type data quality summary (one row per PUPRN and read type).
- `mock_internal/` contains helper/mock-only files, including the master PUPRN list used to align all datasets.
- `serl_climate_data_edition08/` is populated only when weather download is enabled and CDS access is configured.
- Filename suffixes (`edition08`, year values) come from `config/serl_mock.yaml` and generator defaults.

### Consistency caveat for dummy data

This project generates synthetic data in multiple generator modules. As with most mock systems,
some combinations of values across datasets may not be fully realistic or perfectly coherent in
all edge cases.

The code does enforce consistency for key links used in downstream testing, including:

- Shared PUPRN identity across outputs.
- Deterministic household trait assignment from `mock_internal/household_traits.csv`.
- PV ownership consistency across contextual fields and exporter list generation.
- Meter-trait consistency for gas/export availability in smart-meter and read-type summary outputs.
- Solar-thermal trait consistency between `household_traits.csv` and the EPC / survey solar-water-heating fields.
- Nation assignment (England & Wales vs Scotland) shared between EPC records and the participant summary's `Region`.

---

## Quick start

Use this path if you want to generate mock smart-meter and contextual datasets without downloading weather data.

1. Clone the repository:

```bash
git clone https://github.com/BarsantiMatteo/serl-mock.git
cd serl-mock
```

2. Install uv (if not already installed):

**Windows (PowerShell):**
```bash
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

3. Sync the project environment:

```bash
uv sync
```

4. Run the generator:

```bash
uv run python scripts/generate_mock_data.py --skip-weather
```

This command generates all mock datasets except ERA5 weather files.

All settings (number of households, time period, random seed, consumption parameters)
are controlled by a single configuration file:

**[config/serl_mock.yaml](config/serl_mock.yaml)**

Key options you can adjust before running the generator:

| Setting | Description | Default |
|---|---|---|
| `n_households` | Number of synthetic households to generate | `100` |
| `seed` | Random seed for reproducible outputs | `42` |
| `edition` | Dataset edition suffix used in output filenames | `"08"` |
| `start_year` / `end_year` | Time period for smart-meter data | `2019` / `2019` |
| `household_traits.pv_fraction` | Share of households with PV | `0.15` |
| `household_traits.hp_fraction` | Share of households with a heat pump | `0.07` |
| `household_traits.ev_fraction` | Share of households with an EV | `0.10` |
| `household_traits.gas_meter_fraction` | Share of households with a gas meter | `0.85` |
| `household_traits.export_meter_fraction` | Share of households with an export meter | `0.15` |
| `household_traits.solar_thermal_fraction` | Share of households with solar thermal (solar water heating) | `0.02` |
| `profiles.base_elec_mean_wh` | Mean baseline electricity per half-hour period (Wh) | `175` |
| `profiles.base_gas_mean_wh` | Mean baseline gas at peak heating demand (Wh) | `1500` |
| `patterns.elec_seasonal_amplitude` | Seasonal swing around annual mean electricity | `0.3` |
| `patterns.gas_seasonal_amplitude` | Winter gas heating multiplier | `2.0` |
| `weather.*` | ERA5/CDS download settings (dataset, variables, bounding box) | see file |

See [docs/02_configuration.md](docs/02_configuration.md) for a full description of every option.

### Weather data (ERA5 via CDS API)

If you also want weather data, the same generator can download ERA5 hourly reanalysis data from the
[Copernicus Climate Data Store](https://cds.climate.copernicus.eu/how-to-api) and convert it to SERL-style monthly CSV files.

Before running with weather enabled, complete these one-time steps:

1. Register for a free CDS account.
2. Accept the ERA5 dataset licence terms in the CDS portal.
3. Create `~/.cdsapirc` with your credentials:

```
url: https://cds.climate.copernicus.eu/api
key: <your-api-key>
```

Then run the full pipeline:

```bash
uv run python scripts/generate_mock_data.py
```

If CDS setup is not available yet, continue using:

```bash
uv run python scripts/generate_mock_data.py --skip-weather
```


---

## Documentation

| Topic | File |
|---|---|
| What the project does, key concepts | [docs/00_overview.md](docs/00_overview.md) |
| Project layout and module descriptions | [docs/01_structure.md](docs/01_structure.md) |
| How smart-meter values are generated | [docs/03_generation_model.md](docs/03_generation_model.md) |
| All configuration options | [docs/02_configuration.md](docs/02_configuration.md) |
| SERL dataset column reference | [docs/04_metadata.md](docs/04_metadata.md) |
| EPC quirks and region differences (England & Wales vs Scotland) | [docs/05_epc_reference.md](docs/05_epc_reference.md) |
| Working notes and TODOs | [docs/06_working_notes.md](docs/06_working_notes.md) |

---

## Generated data is not real

The output files are for **pipeline testing and local development only**. They do not represent real households or real consumption, and should never be used for analysis or reporting.

---

## Citation

If you use this package in your work, please cite it using the Zenodo DOI:

[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.20544813-blue)](https://doi.org/10.5281/zenodo.20544813)

```bibtex
@software{serl_mock,
  author  = {Barsanti, Matteo},
  title   = {serl-mock: Dummy version of SERL Observatory data for pipeline development and testing},
  year    = {2026},
  doi     = {10.5281/zenodo.20544813},
  url     = {https://github.com/BarsantiMatteo/serl-mock}
}
```


---

## Contributing

Bug reports, suggestions, and pull requests are welcome.

- **Questions or feedback** — open a [GitHub issue](https://github.com/BarsantiMatteo/serl-mock/issues) or email [m.barsanti@ucl.ac.uk](mailto:m.barsanti@ucl.ac.uk).
- **Bug fixes or improvements** — fork the repository, make your changes on a branch, and open a pull request against `master`.

---

## License

This project is released under the [MIT License](https://opensource.org/license/mit).
