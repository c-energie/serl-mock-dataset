"""
Generate mock SERL smart-meter and contextual data.

Loads settings from config/serl_mock.yaml and produces:

  1. puprn_master.csv               — shared household ID list
     household_traits.csv           — household traits (PV/HP/EV + meter types)
  2. Monthly half-hourly CSVs       — realistic electricity and gas time series
                                       with seasonal and intraday patterns
                                       (see src/serl_mock/patterns.py)
  3. Yearly daily CSVs              — daily sums of electricity and gas,
                                       one file per calendar year
  4. ERA5 weather data              — hourly NetCDF files downloaded from the
                                       Copernicus Climate Data Store (CDS API)
                                       and converted to CSV files in the SERL
                                       climate data schema
  5. Contextual datasets            — EPC, survey, participant summary,
                                       follow-up survey, list of exporters
                                       (participant summary includes LSOA and
                                       ERA5 grid_cell per household, derived
                                       from the same grid spec used to download
                                       the weather data)

Output is saved under data/mock/.  The generated data is not real SERL data
and is intended only for pipeline testing and local development outside the
Trusted Research Environment.

Weather download requires CDS API credentials in ~/.cdsapirc (or via the
CDSAPI_URL / CDSAPI_KEY environment variables).  Pass --skip-weather to skip
the download step if credentials are not available.
"""

# --- src-layout shim ---
import argparse
import shutil
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # repo root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.serl_mock.paths import CONFIG_DIR, MOCK_DIR, MOCK_HH_DIR, MOCK_DAILY_DIR, MOCK_INTERNAL_DIR, MOCK_AGGREGATED_DIR, REFERENCE_DIR, MOCK_CLIMATE_DIR
from src.serl_mock.ids import make_alphanumeric_ids_ordered, write_puprn_list_csv, load_puprn_list_csv
from src.serl_mock.generator_smartmeter import HHSmartMeterGenerator, DailySmartMeterGenerator, ReadTypeDataQualitySummaryGenerator
from src.serl_mock.generator_contextual_data import SERLContextualVariablesGenerator
from src.serl_mock.generator_household_traits import generate_household_traits, write_household_traits
from src.serl_mock.weather_downloader import WeatherDownloader
from src.serl_mock.utils import read_config



def run_all(skip_weather: bool = False, survey_only: bool = False):
    cfg_path = CONFIG_DIR / "serl_mock.yaml"
    cfg = read_config(cfg_path)

    edition = str(cfg.get("edition", "08")).zfill(2)

    # Ensure target folders exist
    MOCK_DIR.mkdir(parents=True, exist_ok=True)
    MOCK_HH_DIR.mkdir(parents=True, exist_ok=True)
    MOCK_DAILY_DIR.mkdir(parents=True, exist_ok=True)
    MOCK_INTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    MOCK_AGGREGATED_DIR.mkdir(parents=True, exist_ok=True)

    print("\nStep 0: Copying reference files to mock folder")
    # Files copied as-is (name unchanged)
    for fname in ["bst_dates_to_2030.csv"]:
        src = REFERENCE_DIR / fname
        if src.exists():
            shutil.copy2(src, MOCK_DIR / fname)
            print(f"  Copied {fname}")
    # Data dictionaries: copy from reference with edition suffix updated
    dict_renames = {
        "serl_survey_data_dictionary_edition07.csv":
            f"serl_survey_data_dictionary_edition{edition}.csv",
        "serl_covid19_survey_data_dictionary_edition07.csv":
            f"serl_covid19_survey_data_dictionary_edition{edition}.csv",
    }
    for src_name, dst_name in dict_renames.items():
        src = REFERENCE_DIR / src_name
        if src.exists():
            shutil.copy2(src, MOCK_DIR / dst_name)
            print(f"  Copied {src_name} -> {dst_name}")

    print("\nStep 0b: Creating placeholder files")
    placeholders = [
        f"serl_tariff_data_edition{edition}.csv",
        "serl_energy_use_in_GB_domestic_buildings_2021_aggregated_statistics_edition07.csv",
    ]
    for fname in placeholders:
        p = MOCK_DIR / fname
        if not p.exists():
            p.write_text("# placeholder\n", encoding="utf-8")
            print(f"  Created {fname}")

    puprn_csv = MOCK_INTERNAL_DIR / "puprn_master.csv"
    traits_csv = MOCK_INTERNAL_DIR / "household_traits.csv"

    if survey_only:
        if not puprn_csv.exists() or not traits_csv.exists():
            sys.exit(
                "ERROR: --survey-only requires a previous full run. "
                f"Missing: {', '.join(str(p) for p in [puprn_csv, traits_csv] if not p.exists())}"
            )
        print("  Skipping steps 1-4 (--survey-only).")
    else:
        print("\nStep 1: Generating PUPRNs and household device traits")
        puprns = make_alphanumeric_ids_ordered(
            n=cfg.get("n_households", 100),
            seed=cfg.get("seed", 42),
            length=cfg.get("puprn", {}).get("length", 8)
        )
        write_puprn_list_csv(puprns, puprn_csv)
        print(f"  {len(puprns)} PUPRNs written to {puprn_csv}")

        traits_cfg = cfg.get("household_traits", {})
        traits_df = generate_household_traits(
            puprns=puprns,
            pv_fraction=float(traits_cfg.get("pv_fraction", 0.07)),
            hp_fraction=float(traits_cfg.get("hp_fraction", 0.0)),
            ev_fraction=float(traits_cfg.get("ev_fraction", 0.0)),
            gas_meter_fraction=float(traits_cfg.get("gas_meter_fraction", 0.85)),
            export_meter_fraction=float(traits_cfg.get("export_meter_fraction", 0.15)),
            solar_thermal_fraction=float(traits_cfg.get("solar_thermal_fraction", 0.0)),
            seed=cfg.get("seed", 42),
            edition=edition,
        )
        write_household_traits(traits_df, traits_csv)
        print(f"  Household traits written to {traits_csv}")
        print(f"    PV households: {(traits_df['has_pv'] == 1).sum()}")
        print(f"    HP households: {(traits_df['has_hp'] == 1).sum()}")
        print(f"    EV households: {(traits_df['has_ev'] == 1).sum()}")
        print(f"    Solar thermal households: {(traits_df['has_solar_thermal'] == 1).sum()}")
        print(f"    Gas meter households: {(traits_df['has_gas_meter'] == 1).sum()}")
        print(f"    Export meter households: {(traits_df['has_export_meter'] == 1).sum()}")

        print(f"\nStep 2: Generating half-hourly smart meter data "
              f"({cfg.get('start_year')}–{cfg.get('end_year')}, "
              f"edition {cfg.get('edition', '08')})")
        gen_sm = HHSmartMeterGenerator(
            config_path=str(cfg_path),
            puprn_list_path=str(puprn_csv),
        )
        gen_sm.generate_all(outfolder=MOCK_HH_DIR)

        print(f"\nStep 3: Generating daily smart meter data "
              f"({cfg.get('start_year')}–{cfg.get('end_year')}, "
              f"edition {cfg.get('edition', '08')})")
        gen_daily = DailySmartMeterGenerator(
            config_path=str(cfg_path),
            puprn_list_path=str(puprn_csv),
        )
        gen_daily.generate_all(outfolder=MOCK_DAILY_DIR)

        print(f"\nStep 3b: Generating read-type data quality summary "
              f"(edition {cfg.get('edition', '08')})")
        gen_rt = ReadTypeDataQualitySummaryGenerator(
            config_path=str(cfg_path),
            puprn_list_path=str(puprn_csv),
        )
        gen_rt.generate_and_write(
            hh_folder=MOCK_HH_DIR,
            daily_folder=MOCK_DAILY_DIR,
            outfolder=MOCK_DIR,
        )

    print("\nStep 4: Downloading ERA5 weather data and converting to CSV")
    if survey_only or skip_weather:
        print("  Skipped.")
    else:
        try:
            dl = WeatherDownloader(config_path=str(cfg_path))
            dl._get_client()  # fail fast: surface credential / connectivity errors now
        except Exception as exc:
            print(f"  WARNING: CDS API unavailable — {exc}")
            print(f"  Skipping weather download; placeholder folder created at {MOCK_CLIMATE_DIR}")
            MOCK_CLIMATE_DIR.mkdir(parents=True, exist_ok=True)
        else:
            nc_count = 0
            csv_count = 0
            for year in range(dl.start_year, dl.end_year + 1):
                for month in range(1, 13):
                    try:
                        nc_path, csv_path = dl.ensure_month(year, month)
                        if nc_path.exists():
                            nc_count += 1
                        if csv_path.exists():
                            csv_count += 1
                    except Exception as exc:
                        print(f"  WARNING: {year}-{month:02d} failed — {exc}")
            print(f"  {nc_count} NetCDF file(s) and {csv_count} CSV file(s) in {dl.output_dir}")

    print("\nStep 5: Generating contextual variables")
    gen_ctx = SERLContextualVariablesGenerator(
        config_path=str(cfg_path),
        puprn_list_path=str(puprn_csv),
    )
    gen_ctx.write_all(outfolder=MOCK_DIR, mock_only_outfolder=MOCK_INTERNAL_DIR)

    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate mock SERL datasets.")
    parser.add_argument(
        "--skip-weather",
        action="store_true",
        help="Skip the ERA5 weather data download step (step 4).",
    )
    parser.add_argument(
        "--survey-only",
        action="store_true",
        help="Regenerate only the survey/contextual data (step 5). Requires a prior full run.",
    )
    args = parser.parse_args()
    run_all(skip_weather=args.skip_weather, survey_only=args.survey_only)

