# src/serl_mock/generator_contextual_data.py
from __future__ import annotations
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union, cast

import numpy as np
import pandas as pd

from .ids import (
    make_alphanumeric_ids_ordered,
    load_puprn_list_csv,
)
from .generator_household_traits import load_household_traits
from .paths import MOCK_CLIMATE_DIR
from .utils import (
    read_config, seed_random, ensure_output_dir,
    with_edition_suffix, write_csv, read_survey_dictionary
)

@dataclass
class OutputNames:
    epc: str = "serl_epc_data"
    survey: str = "serl_survey_data"
    covid19_survey: str = "serl_covid19_survey_data"
    summary: str = "serl_participant_summary"
    followup_survey: str = "serl_2023_follow_up_survey_data"
    exporters_prefix: str = "Elec"

class SERLContextualVariablesGenerator:
    """
    Generates contextual datasets:
      - EPC data
      - SERL survey data
      - Participant summary
      - Follow-up survey
      - List of exporter PUPRNs
    Uses shared config and PUPRN utilities to align with smart-meter HH data.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        n_households: Optional[int] = None,
        year: Optional[int] = None,
        outfolder: Optional[str] = None,
        seed: Optional[int] = None,
        puprn_list_path: Optional[str] = None,
        write_puprn_list: bool = False,
    ):
        cfg = read_config(config_path)

        # Core parameters
        self.n_households = n_households or cfg.get("n_households", 100)
        self.year = year or cfg.get("year", 2023)

        # RNG
        self.seed = seed if seed is not None else cfg.get("seed", 42)
        seed_random(self.seed)

        # Edition & filenames
        fcfg = cfg.get("filenames", {})
        self.names = OutputNames(
            epc=fcfg.get("epc", "serl_epc_data"),
            survey=fcfg.get("survey", "serl_survey_data"),
            covid19_survey=fcfg.get("covid19_survey", "serl_covid19_survey_data"),
            summary=fcfg.get("summary", "serl_participant_summary"),
            followup_survey=fcfg.get("followup_survey", "serl_2023_follow_up_survey_data"),
            exporters_prefix=fcfg.get("exporters_prefix", "Elec"),
        )
        self.edition = str(cfg.get("edition", "")).strip() or None

        # Household traits (PV/HP/EV) — load from pre-generated CSV
        traits_path = cfg.get("household_traits_path")
        if not traits_path:
            # Try default location in mock_internal
            from .paths import MOCK_INTERNAL_DIR
            traits_path = MOCK_INTERNAL_DIR / "household_traits.csv"
        self.traits_path = str(traits_path)

        # Household-trait fractions are used during household traits generation,
        # not directly in this contextual generator.

        # Survey dictionary paths
        self.survey_dictionary_path = cfg.get(
            "survey_dictionary_path",
            "data/reference/serl_survey_data_dictionary_edition07.csv",
        )
        self.followup_survey_dictionary_path = cfg.get(
            "followup_survey_dictionary_path",
            "data/reference/serl_follow_up_survey_data_dictionary_edition07.csv",
        )
        self.covid19_survey_dictionary_path = cfg.get(
            "covid19_survey_dictionary_path",
            "data/reference/serl_covid19_survey_data_dictionary_edition07.csv",
        )

        # PUPRN: load from master list or generate deterministically
        self.puprn_list_path = puprn_list_path or cfg.get("puprn_list_path")

        if self.puprn_list_path:
            puprns = load_puprn_list_csv(self.puprn_list_path)
            if len(puprns) < self.n_households:
                raise ValueError("PUPRN list smaller than n_households.")
            self.puprns = puprns[: self.n_households]
        else:
            self.puprns = make_alphanumeric_ids_ordered(self.n_households, length=8, seed=self.seed)

        # Load household traits from CSV
        traits_df = load_household_traits(self.traits_path)
        self._pv_households = set(traits_df[traits_df['has_pv'] == 1].index.tolist())
        self._hp_households = set(traits_df[traits_df['has_hp'] == 1].index.tolist())
        self._ev_households = set(traits_df[traits_df['has_ev'] == 1].index.tolist())
        self._solar_thermal_households = (
            set(traits_df[traits_df['has_solar_thermal'] == 1].index.tolist())
            if 'has_solar_thermal' in traits_df.columns else set()
        )

        # ERA5 grid-cell assignment — used in participant summary.
        # Prefer sampling from grid cells that actually appear in the downloaded
        # climate CSVs so every assigned cell is guaranteed to be joinable.
        # Falls back to the geometric approach if no CSVs exist yet.
        wcfg = cfg.get("weather", {})
        climate_dir = Path(wcfg.get("output_dir", str(MOCK_CLIMATE_DIR)))
        available_cells = self._read_climate_grid_cells(climate_dir)
        if available_cells:
            rng = np.random.default_rng(self.seed)
            chosen = rng.choice(available_cells, size=len(self.puprns))
            self._grid_cells: Dict[str, str] = {
                puprn: str(cell) for puprn, cell in zip(self.puprns, chosen)
            }
        else:
            area = wcfg.get("area", [60.0, -8.0, 49.0, 2.0])
            grid = wcfg.get("grid", [0.25, 0.25])
            self._grid_cells = self._assign_grid_cells(
                puprns=list(self.puprns),
                area=area,
                grid=grid,
                seed=self.seed,
            )

        # Nation assignment (England & Wales vs Scotland) — shared across EPC
        # generation and the participant summary so a PUPRN's epcVersion and
        # Region never contradict each other. Matches the real edition07
        # split (~91.3% England and Wales / ~8.6% Scotland).
        nation_rnd = random.Random(self.seed + 700)
        self._nation: Dict[str, str] = {
            puprn: nation_rnd.choices(
                ['England and Wales', 'Scotland'], weights=[91.3, 8.6], k=1
            )[0]
            for puprn in self.puprns
        }

    # ---------- Grid-cell assignment ----------
    @staticmethod
    def _read_climate_grid_cells(climate_dir: Path) -> List[str]:
        """Return sorted unique grid_cell values from the first climate CSV found.

        ERA5 grid coverage is identical across months, so reading one file is
        sufficient and avoids scanning every monthly CSV.  Returns [] when no
        CSVs are present (climate data not yet downloaded).
        """
        csv_files = sorted(climate_dir.glob("*.csv"))
        if not csv_files:
            return []
        try:
            df = pd.read_csv(csv_files[0], usecols=["grid_cell"])
            return sorted(df["grid_cell"].dropna().unique().tolist())
        except Exception:
            return []

    @staticmethod
    def _assign_grid_cells(
        puprns: List[str],
        area: List[float],
        grid: List[float],
        seed: int,
    ) -> Dict[str, str]:
        """Geometric fallback: assign each PUPRN to a randomly sampled ERA5 grid cell.

        Used only when climate CSVs are not yet available.  Locations are drawn
        uniformly within *area* and snapped to the nearest grid point.  The
        ``grid_cell`` identifier uses the same zero-padded ``<col>_<row>`` format
        as the SERL climate data schema (e.g. ``03_01``).
        """
        north, west, south, east = (
            float(area[0]), float(area[1]), float(area[2]), float(area[3])
        )
        lat_step, lon_step = float(grid[0]), float(grid[1])
        max_row = math.floor((north - south) / lat_step)
        max_col = math.floor((east - west) / lon_step)

        rng = np.random.default_rng(seed)
        raw_lats = rng.uniform(south, north, size=len(puprns))
        raw_lons = rng.uniform(west, east, size=len(puprns))

        result: Dict[str, str] = {}
        for puprn, raw_lat, raw_lon in zip(puprns, raw_lats, raw_lons):
            row = max(0, min(round((north - float(raw_lat)) / lat_step), max_row))
            col = max(0, min(round((float(raw_lon) - west) / lon_step), max_col))
            result[puprn] = f"{col:02d}_{row:02d}"
        return result

    # ---------- File naming ----------
    def _fname(self, basename: str) -> str:
        return with_edition_suffix(basename, self.edition)

    # ---------- EPC ----------
    @staticmethod
    def _epc_fields() -> List[str]:
        return [
            'PUPRN', 'builtForm', 'co2EmissCurrPerFloorArea', 'co2EmissionsCurrent',
            'co2EmissionsPotential', 'constituency', 'constructionAgeBand',
            'currentEnergyEfficiency', 'currentEnergyRating', 'energyConsumptionCurrent',
            'energyConsumptionPotential', 'energyTariff', 'environmentImpactCurrent',
            'environmentImpactPotential', 'extensionCount', 'fixedLightingOutletsCount',
            'flatStoreyCount', 'flatTopStorey', 'floorDescription', 'floorEnergyEff',
            'floorEnvEff', 'floorHeight', 'floorLevel', 'glazedArea', 'glazedType',
            'heatingCostCurrent', 'heatingCostPotential', 'heatLossCorridor',
            'hotWaterCostCurrent', 'hotWaterCostPotential', 'hotwaterDescription',
            'hotWaterEnergyEff', 'hotWaterEnvEff', 'inspectionDate', 'lightingCostCurrent',
            'lightingCostPotential', 'lightingDescription', 'lightingEnergyEff',
            'lightingEnvEff', 'localAuthority', 'lodgementDate', 'lodgementDatetime',
            'lowEnergyFixedLightCount', 'lowEnergyLighting', 'mainFuel',
            'mainheatcEnergyEff', 'mainheatcEnvEff', 'mainheatcontDescription',
            'mainheatDescription', 'mainheatEnergyEff', 'mainheatEnvEff',
            'mainHeatingControls', 'mainsGasFlag', 'mechanicalVentilation',
            'multiGlazeProportion', 'numberHabitableRooms', 'numberHeatedRooms',
            'numberOpenFireplaces', 'photoSupply', 'potentialEnergyEfficiency',
            'potentialEnergyRating', 'propertyType', 'roofDescription', 'roofEnergyEff',
            'roofEnvEff', 'secondheatDescription', 'sheatingEnergyEff', 'sheatingEnvEff',
            'solarWaterHeatingFlag', 'tenure', 'totalFloorArea', 'transactionType',
            'unheatedCorridorLength', 'wallsDescription', 'wallsEnergyEff', 'wallsEnvEff',
            'windowsDescription', 'windowsEnergyEff', 'windowsEnvEff', 'windTurbineCount',
            'epcVersion',
        ]

    def generate_epc(self) -> pd.DataFrame:
        fields = self._epc_fields()
        energy_ratings = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
        property_types = ['House', 'Flat', 'Maisonette', 'Bungalow']
        built_forms = ['Detached', 'Semi-Detached', 'Mid-Terrace', 'End-Terrace',
                       'Enclosed Mid-Terrace', 'Enclosed End-Terrace']
        efficiency_ratings = ['Very Good', 'Good', 'Average', 'Poor', 'Very Poor']
        # floorEnergyEff: mostly a single rating, occasionally "N/A". Multi-
        # element "N/A | N/A" values only occur on Scotland-schema records —
        # per the SERL EPC technical documentation, Scotland reports each
        # element of a multi-part floor separately, joined by "|", whereas
        # England & Wales always reports a single element.
        floor_energy_eff_singles = ['Average', 'Good', 'N/A', 'Poor', 'Very Good', 'Very Poor']
        floor_energy_eff_single_weights = [19, 19, 4, 19, 19, 19]
        floor_energy_eff_combos = ['N/A | N/A', 'N/A | N/A | N/A']
        floor_energy_eff_combo_weights = [0.5, 0.5]
        # roofEnergyEff: same single-vs-Scotland-multi-element convention as
        # floorEnergyEff above. Single ratings dominate; multi-element
        # combinations (including the mixed "N/A | Very Poor" case) only
        # occur for Scotland rows.
        _roof_ratings = ['Average', 'Good', 'Poor', 'Very Good', 'Very Poor']
        roof_energy_eff_singles = list(_roof_ratings)
        roof_energy_eff_single_weights = [17] * len(roof_energy_eff_singles)
        roof_energy_eff_pairs = [
            f"{a} | {b}"
            for i, a in enumerate(_roof_ratings)
            for b in _roof_ratings[i:]
        ]
        roof_energy_eff_triples = [
            f"{a} | {b} | {c}"
            for i, a in enumerate(_roof_ratings)
            for j, b in enumerate(_roof_ratings[i:], start=i)
            for c in _roof_ratings[j:]
        ]
        roof_energy_eff_combos = roof_energy_eff_pairs + roof_energy_eff_triples + ['N/A | Very Poor']
        roof_energy_eff_combo_weights = (
            [1] * len(roof_energy_eff_pairs) + [0.3] * len(roof_energy_eff_triples) + [0.5]
        )
        # glazedArea: England & Wales EPCs describe glazed area with text
        # labels; Scotland EPCs report a numeric band code with different
        # value semantics (0=New dwelling, 1=Typical, 2=More than typical,
        # 4=Much more than typical, 5=Much less than typical), per the
        # Scotland EPC data dictionary.
        glazed_area_ew_values = [
            'Less Than Typical', 'More Than Typical', 'Much Less Than Typical',
            'Much More Than Typical', 'NO DATA!', 'Normal',
        ]
        glazed_area_ew_weights = [6, 6, 3, 3, 2, 70]
        glazed_area_scotland_values = ['0', '1', '2', '4', '5', 'NO DATA!']
        glazed_area_scotland_weights = [2, 70, 10, 5, 5, 2]
        yes_no_flags = ['Y', 'N']
        fuel_types = ['mains gas (not community)', 'electricity', 'oil', 'solid fuel']
        tenure_types = ['owner-occupied', 'rented (private)', 'rented (social)']
        mech_vent_types = ['natural', 'mechanical, extract only',
                           'mechanical, supply and extract', 'NO DATA!']
        heat_loss_types = ['no corridor', 'heated corridor', 'unheated corridor', 'NO DATA!']
        # constructionAgeBand boundaries differ by nation, per each nation's
        # EPC data dictionary (Scotland's bands are offset from E&W's). In the real
        # edition08 SERL EPC data, England & Wales values carry an "England and
        # Wales: " prefix (e.g. "England and Wales: 1950-1966"); Scotland's values
        # do not carry a nation prefix. Both nations can also be NULL, INVALID, or
        # "NO DATA!" instead of a band.
        age_bands_ew = ['England and Wales: before 1900', 'England and Wales: 1900-1929',
                        'England and Wales: 1930-1949', 'England and Wales: 1950-1966',
                        'England and Wales: 1967-1975', 'England and Wales: 1976-1982',
                        'England and Wales: 1983-1990', 'England and Wales: 1991-1995',
                        'England and Wales: 1996-2002', 'England and Wales: 2003-2006',
                        'England and Wales: 2007-2011', 'England and Wales: 2012 onwards',
                        'NULL', 'INVALID', 'NO DATA!']
        age_bands_ew_weights = [8] * 12 + [1, 1, 1]
        age_bands_scotland = ['before 1919', '1919-1929', '1930-1949', '1950-1964',
                              '1965-1975', '1976-1983', '1984-1991', '1992-1998',
                              '1999-2002', '2003-2007', '2008-2011', '2012 onwards',
                              'NULL', 'INVALID', 'NO DATA!']
        age_bands_scotland_weights = [8] * 12 + [1, 1, 1]
        transaction_types = ['marketed sale', 'rental', 'new dwelling',
                             'following green deal', 'assessment for green deal']

        data = []
        rnd = random.Random(self.seed + 100)

        for puprn in self.puprns:
            nation = self._nation[puprn]
            row: Dict[str, object] = {'PUPRN': puprn}
            for field in fields[1:]:
                fl = field.lower()
                if 'rating' in fl and 'efficiency' not in fl:
                    row[field] = np.nan if rnd.random() < 0.3 else rnd.choice(energy_ratings)
                elif 'efficiency' in fl:
                    row[field] = rnd.randint(20, 100)
                elif field == 'propertyType':
                    row[field] = rnd.choice(property_types)
                elif field == 'builtForm':
                    row[field] = rnd.choice(built_forms)
                elif field == 'epcVersion':
                    row[field] = nation
                elif field == 'mechanicalVentilation':
                    row[field] = rnd.choice(mech_vent_types)
                elif field == 'heatLossCorridor':
                    row[field] = rnd.choice(heat_loss_types)
                elif field == 'constructionAgeBand':
                    if nation == 'Scotland':
                        row[field] = rnd.choices(age_bands_scotland, weights=age_bands_scotland_weights, k=1)[0]
                    else:
                        row[field] = rnd.choices(age_bands_ew, weights=age_bands_ew_weights, k=1)[0]
                elif field == 'transactionType':
                    row[field] = rnd.choice(transaction_types)
                elif field == 'mainFuel':
                    row[field] = rnd.choice(fuel_types)
                elif field == 'tenure':
                    row[field] = rnd.choice(tenure_types)
                elif field == 'totalFloorArea':
                    row[field] = np.nan if rnd.random() < 0.3 else round(rnd.uniform(30.0, 300.0), 1)
                elif field == 'floorHeight':
                    row[field] = round(rnd.uniform(2.0, 3.5), 2)
                elif field == 'mainHeatingControls':
                    row[field] = rnd.randint(1000, 9999)
                elif field == 'lowEnergyLighting':
                    row[field] = rnd.randint(0, 100)
                elif field == 'flatTopStorey':
                    row[field] = rnd.choice(yes_no_flags)
                elif field == 'solarWaterHeatingFlag':
                    row[field] = 'Y' if puprn in self._solar_thermal_households else 'N'
                elif field == 'photoSupply':
                    pct = rnd.randint(0, 50)
                    row[field] = f"Array: Roof Area: {pct}%; Connection: not applicable (FGHRS or no PV); |"
                elif 'datetime' in fl:
                    base_dt = pd.Timestamp(2020, 1, 1)
                    row[field] = (base_dt + pd.Timedelta(days=rnd.randint(0, 1000))).strftime('%d/%m/%Y %H:%M')
                elif 'date' in fl:
                    base_date = pd.Timestamp(2020, 1, 1)
                    row[field] = (base_date + pd.Timedelta(days=rnd.randint(0, 1000))).strftime('%d/%m/%Y')
                elif 'emiss' in fl:
                    row[field] = round(rnd.uniform(0.5, 10.0), 1)
                elif 'consumption' in fl:
                    row[field] = rnd.randint(50, 500)
                elif 'environmentimpact' in fl:
                    row[field] = rnd.randint(20, 100)
                elif 'cost' in fl:
                    row[field] = rnd.randint(50, 2000)
                elif 'flag' in fl:
                    row[field] = rnd.choice(yes_no_flags)
                elif field == 'floorEnergyEff':
                    if nation == 'Scotland':
                        values = floor_energy_eff_singles + floor_energy_eff_combos
                        weights = floor_energy_eff_single_weights + floor_energy_eff_combo_weights
                    else:
                        values, weights = floor_energy_eff_singles, floor_energy_eff_single_weights
                    row[field] = rnd.choices(values, weights=weights, k=1)[0]
                elif field == 'roofEnergyEff':
                    if nation == 'Scotland':
                        values = roof_energy_eff_singles + roof_energy_eff_combos
                        weights = roof_energy_eff_single_weights + roof_energy_eff_combo_weights
                    else:
                        values, weights = roof_energy_eff_singles, roof_energy_eff_single_weights
                    row[field] = rnd.choices(values, weights=weights, k=1)[0]
                elif field == 'glazedArea':
                    if nation == 'Scotland':
                        values, weights = glazed_area_scotland_values, glazed_area_scotland_weights
                    else:
                        values, weights = glazed_area_ew_values, glazed_area_ew_weights
                    row[field] = rnd.choices(values, weights=weights, k=1)[0]
                elif 'energyeff' in fl or 'enveff' in fl:
                    row[field] = rnd.choice(efficiency_ratings)
                elif 'count' in fl or 'number' in fl:
                    row[field] = rnd.randint(0, 10)
                elif 'proportion' in fl:
                    row[field] = rnd.randint(0, 100)
                elif 'unheated' in fl:
                    row[field] = round(rnd.uniform(0.0, 10.0), 1)
                elif 'description' in fl:
                    row[field] = f"Sample {field} description"
                else:
                    row[field] = f"Sample_{field}"
            data.append(row)

        return pd.DataFrame(data)

    # ---------- SERL survey ----------
    def generate_serl_survey(self) -> pd.DataFrame:
        survey_fields = read_survey_dictionary(self.survey_dictionary_path)
        survey_versions = ['Wave1', 'Wave2', 'Wave3']
        collection_methods = ['Online', 'Postal']
        languages = ['English', 'Welsh', 'Unknown']
        binary_responses = [0, 1]
        multi_choice_responses = [1, 2, 3, 4, 5]
        missing_codes = [-1, -2, -9]
        # Per-field valid missing codes, derived from serl_survey_data_dictionary.
        # Fields not listed here fall back to missing_codes.
        _field_missing: Dict[str, List] = {
            'A1':    [-2], 'A6': [-2], 'A7': [-9, -2], 'A10': [-2], 'A11': [-2],
            'A13_01': [-2], 'A13_02': [-2],
            'A14':   [-2, -1], 'A1501': [-2, -1], 'A1502': [-2, -1],
            'A401':  [-9], 'A402': [-9], 'A403': [-9],
            'A404':  [-9], 'A405': [-9], 'A406': [-9],
            'B2':    [-2], 'B3': [-9, -2], 'B6': [-2, -4], 'B7': [-2, -1], 'B8': [-2, -1],
            'C301':  [-2], 'C302': [-2], 'C303': [-2],
            'C304':  [-2], 'C305': [-2], 'C306': [-2], 'C307': [-2],
            'C4':    [-2, -1],
            'D1':    [-9, -2], 'D2': [-9, -3, -2], 'D3': [-9, -3, -2],
        }
        # Per-field valid (non-missing) options, derived from serl_survey_data_dictionary.
        # Fields not listed here fall back to multi_choice_responses.
        _field_opts: Dict[str, List] = {
            'A1':    [1, 2],
            'A2':    [1, 2, 3, 4],
            'A5':    [1, 2, 3],
            'A6':    [1, 2],
            'A7':    [1, 2],
            'A8':    [1, 2, 3, 4, 5],
            **{f'A9{i:02d}': [0, 1] for i in range(1, 8)},
            'A10':   [1, 2, 3, 4, 5, 6],
            'A11':   [1, 2],
            'A14':   [1, 2, 3, 4],
            'A1501': [1, 2, 3, 4, 5],
            'A1502': [1, 2, 3, 4, 5],
            **{f'A4{i:02d}': [0, 1] for i in range(1, 7)},
            **{f'A16{i:02d}': [0, 1] for i in range(1, 11)},
            'B2':    [1, 2],
            'B7':    [1, 2],
            'B8':    [1, 2],
            **{f'B10{i:02d}': [0, 1] for i in range(1, 15)},
            'D1':    [1, 2, 3, 4, 5, 6, 7],
            'D2':    [1, 2, 3],
            'D3':    [1, 2, 3, 4, 5, 6, 7],
        }
        # Sum fields derived from binary constituent fields. Computed after those
        # fields are assigned so the sum is always consistent with the row values.
        _C3_individual = ['C301', 'C302', 'C303', 'C304', 'C305', 'C306', 'C307']
        _binary_sum_fields: Dict[str, List[str]] = {
            'A3_sum':         [f'A3{i:02d}' for i in range(1, 11)],
            'A4_sum':         [f'A4{i:02d}' for i in range(1, 7)],
            'A9_sum':         [f'A9{i:02d}' for i in range(1, 8)],
            'A12_Taps_sum':   ['A12_Taps_GB', 'A12_Taps_EH', 'A12_Taps_SWH',
                               'A12_Taps_Other', 'A12_Taps_NA', 'A12_Taps_DK'],
            'A12_Shower_sum': ['A12_Shower_GB', 'A12_Shower_EH', 'A12_Shower_SWH',
                               'A12_Shower_Other', 'A12_Shower_NA', 'A12_Shower_DK'],
            'A16_sum':        [f'A16{i:02d}' for i in range(1, 11)],
            'B10_sum':        [f'B10{i:02d}' for i in range(1, 15)],
            'C3_sum':         _C3_individual,
        }
        # C2 occupant-count fields (14 gender × age-band categories).
        _C2_individual = [
            'C2_Male_0_15',    'C2_Male_16_24',   'C2_Male_25_44',   'C2_Male_45_64',
            'C2_Male_65_74',   'C2_Male_75_84',   'C2_Male_85_plus',
            'C2_Female_0_15',  'C2_Female_16_24', 'C2_Female_25_44', 'C2_Female_45_64',
            'C2_Female_65_74', 'C2_Female_75_84', 'C2_Female_85_plus',
        ]
        _C2_child  = ['C2_Male_0_15', 'C2_Female_0_15']
        _C2_adult  = [
            'C2_Male_16_24',   'C2_Male_25_44',   'C2_Male_45_64',
            'C2_Male_65_74',   'C2_Male_75_84',   'C2_Male_85_plus',
            'C2_Female_16_24', 'C2_Female_25_44', 'C2_Female_45_64',
            'C2_Female_65_74', 'C2_Female_75_84', 'C2_Female_85_plus',
        ]
        _C2_65plus = [
            'C2_Male_65_74',   'C2_Male_75_84',   'C2_Male_85_plus',
            'C2_Female_65_74', 'C2_Female_75_84', 'C2_Female_85_plus',
        ]
        _C2_individual_skip = set(_C2_individual[1:])
        # Age-band weights from ONS 2021 Census (England & Wales).
        # Bands: 0-15, 16-24, 25-44, 45-64, 65-74, 75-84, 85+
        # Equal male/female split: male slots first, female slots second.
        _C2_age_weights = [18, 10, 26, 26, 11, 7, 2]
        _C2_weights = _C2_age_weights + _C2_age_weights

        data = []
        rnd = random.Random(self.seed + 200)

        for puprn in self.puprns:
            row: Dict[str, object] = {'PUPRN': puprn}
            for field in survey_fields[1:]:
                if field == 'Survey_version':
                    row[field] = rnd.choice(survey_versions)
                elif field == 'Recorded_date':
                    base = pd.Timestamp(2021, 1, 1)
                    row[field] = (base + pd.Timedelta(days=rnd.randint(0, 730))).strftime('%Y-%m-%d')
                elif field == 'Collection_method':
                    row[field] = rnd.choice(collection_methods)
                elif field == 'Language':
                    row[field] = rnd.choice(languages)
                elif field == 'B1':
                    row[field] = rnd.choice([-2, 1, 2, 3, 4, 5, 6])
                elif field == 'B2':
                    row[field] = 2 if rnd.random() < 0.03 else 1  # ~3% not self-contained
                elif field == 'B4':
                    row[field] = rnd.choice([-2, 1, 2, 3, 4, 5])
                elif field == 'B5':
                    row[field] = rnd.choice([2, 3, 4])
                elif field == 'B5_err':
                    row[field] = 0
                elif field == 'B9':
                    row[field] = rnd.choice([-2, -1]) if rnd.random() < 0.1 else rnd.randint(1, 7)
                elif field == 'C1_new':
                    c1 = row.get('C1')
                    if isinstance(c1, int) and c1 > 0:
                        if rnd.random() < 0.01:
                            row[field] = max(1, c1 + rnd.choice([-1, 1]))
                        else:
                            row[field] = c1
                    else:
                        row[field] = rnd.randint(1, 4)  # C1 missing/invalid
                elif field == 'C1':
                    if rnd.random() < 0.05:
                        row[field] = -2
                    else:
                        # Approximate ONS 2021 Census England & Wales distribution
                        row[field] = rnd.choices(
                            [1, 2, 3, 4, 5, 6, 7, 8],
                            weights=[29, 34, 15, 13, 6, 2, 1, 0.3],
                        )[0]
                elif field == 'C5':
                    row[field] = rnd.randint(-2, 2)
                elif field == 'D4':
                    row[field] = rnd.choice([-3, -2, -1]) if rnd.random() < 0.15 else rnd.randint(1, 5)
                elif field in _binary_sum_fields:
                    row[field] = sum(1 for f in _binary_sum_fields[field] if row.get(f) == 1)
                elif field.startswith('A3'):
                    if not field.endswith('_err'):
                        row[field] = rnd.choice([0, 1])
                    else:
                        row[field] = True if rnd.random() < 0.1 else False
                elif field in ('A12_Taps_SWH', 'A12_Shower_SWH'):
                    row[field] = 1 if puprn in self._solar_thermal_households else 0
                elif field.startswith('A12_'):
                    row[field] = rnd.choice([0, 1])
                elif field.startswith('A13_'):
                    row[field] = rnd.choice(_field_missing.get(field, [-2])) if rnd.random() < 0.1 else rnd.choice([1, 2, 3, 4, 5, 6])
                elif field == 'C2_Male_0_15':
                    # Batch-generate all 14 C2 occupant-count fields together so
                    # their sum matches C1_new. A 5% error rate introduces occasional
                    # mismatches, reflected in C2_sum_diff / C2_error below.
                    target = cast(int, row.get('C1_new', 2))
                    if rnd.random() < 0.05:
                        target = max(0, target + rnd.choice([-1, 1]))
                    counts = [0] * 14
                    for slot in rnd.choices(range(14), weights=_C2_weights, k=target):
                        counts[slot] += 1
                    for i, f in enumerate(_C2_individual):
                        row[f] = counts[i]
                elif field in _C2_individual_skip:
                    pass  # already set by C2_Male_0_15 handler
                elif field == 'C2_sum':
                    row[field] = sum(max(0, cast(int, row.get(f, 0))) for f in _C2_individual)
                elif field == 'C2_sum_diff':
                    row[field] = cast(int, row.get('C2_sum', 0)) - cast(int, row.get('C1_new', 0))
                elif field == 'C2_error':
                    row[field] = cast(int, row.get('C2_sum_diff', 0)) != 0
                elif field == 'C2_tot_child':
                    row[field] = sum(max(0, cast(int, row.get(f, 0))) for f in _C2_child)
                elif field == 'C2_tot_adult':
                    row[field] = sum(max(0, cast(int, row.get(f, 0))) for f in _C2_adult)
                elif field == 'C2_tot_65_plus':
                    row[field] = sum(max(0, cast(int, row.get(f, 0))) for f in _C2_65plus)
                elif field == 'C2_all_65_plus':
                    c2s = cast(int, row.get('C2_sum', 0))
                    row[field] = 1 if c2s > 0 and cast(int, row.get('C2_tot_65_plus', 0)) == c2s else 0
                elif field == 'C301':
                    # Batch-generate all 7 C3 working-status fields. The number
                    # selected sums to C2_tot_adult in ~95% of cases.
                    n_adults = cast(int, row.get('C2_tot_adult', 0))
                    target = min(n_adults, 7)
                    if rnd.random() < 0.05:
                        target = max(0, min(7, target + rnd.choice([-1, 1])))
                    selected = set(rnd.sample(range(7), target))
                    for i, f in enumerate(_C3_individual):
                        row[f] = 1 if i in selected else 0
                elif field in ('C302', 'C303', 'C304', 'C305', 'C306', 'C307'):
                    pass  # already set by C301 handler
                elif field == 'C3_sum_diff':
                    row[field] = cast(int, row.get('C3_sum', 0)) - cast(int, row.get('C2_tot_adult', 0))
                elif field == 'C3_error':
                    row[field] = cast(int, row.get('C3_sum_diff', 0)) != 0
                elif field == 'None_working':
                    n_adults = cast(int, row.get('C2_tot_adult', 0))
                    c3s = cast(int, row.get('C3_sum', 0))
                    if n_adults == 0:
                        row[field] = None   # not possible to determine
                    elif c3s == 0:
                        row[field] = 1      # no adults working
                    else:
                        row[field] = 0      # some adults working
                elif field == 'C4':
                    n_adults = cast(int, row.get('C2_tot_adult', 0))
                    if rnd.random() < 0.05:
                        row[field] = rnd.choice(_field_missing.get('C4', [-2, -1]))
                    else:
                        row[field] = rnd.randint(0, max(0, n_adults))
                elif field == 'C4_error':
                    c4 = row.get('C4')
                    c1_new = cast(int, row.get('C1_new', 0))
                    row[field] = isinstance(c4, int) and c4 >= 0 and c4 > c1_new
                elif field.startswith('C2_'):
                    row[field] = -2 if rnd.random() < 0.05 else rnd.randint(0, 3)
                elif '_text' in field.lower() or '_other' in field.lower():
                    row[field] = f"Sample text for {field}"
                elif field.endswith('_sum') or field.endswith('_diff'):
                    row[field] = rnd.randint(0, 10)
                elif field.endswith('_err') or field.endswith('_edit'):
                    row[field] = rnd.choice([True, False])
                elif field.startswith('A') and field[1:].isdigit():
                    row[field] = rnd.choice(_field_missing.get(field, missing_codes)) if rnd.random() < 0.1 else rnd.choice(_field_opts.get(field, multi_choice_responses))
                elif field.startswith('B') and field[1:].isdigit():
                    row[field] = rnd.choice(_field_missing.get(field, missing_codes)) if rnd.random() < 0.05 else rnd.choice(_field_opts.get(field, multi_choice_responses))
                elif field == 'C6':
                    row[field] = (rnd.choice([-9, -2, -1]) if rnd.random() < 0.1
                                  else rnd.choice([1, 2, 3, 4, 5])) if puprn in self._ev_households else -9
                elif field.startswith('C') and field[1:].isdigit():
                    row[field] = rnd.choice(_field_missing.get(field, missing_codes)) if rnd.random() < 0.05 else rnd.randint(0, 15)
                elif field.startswith('D') and field[1:].isdigit():
                    row[field] = rnd.choice(_field_missing.get(field, missing_codes)) if rnd.random() < 0.15 else rnd.choice(_field_opts.get(field, multi_choice_responses))
                elif field.endswith('01') or field.endswith('02') or field.endswith('03'):
                    row[field] = rnd.choice(binary_responses)
                else:
                    row[field] = rnd.choice(missing_codes) if rnd.random() < 0.08 else rnd.choice(multi_choice_responses)

            # Device-aware overrides from config fractions.
            # These are applied after generic random generation to keep consistency.
            if 'A1607' in row:
                row['A1607'] = 1 if puprn in self._hp_households else 0
            if 'C5' in row:
                row['C5'] = 1 if puprn in self._ev_households else 2
            if 'C6' in row:
                row['C6'] = rnd.choice([1, 2, 3, 4]) if puprn in self._ev_households else -9
            data.append(row)

        return pd.DataFrame(data)

    # ---------- COVID-19 survey ----------
    def generate_covid19_survey(self) -> pd.DataFrame:  # noqa: C901
        # Field list comes from the data dictionary — mirrors how generate_serl_survey works.
        fields = read_survey_dictionary(self.covid19_survey_dictionary_path)

        missing_codes = [-1, -2, -9]

        # Fields with a direct option-set lookup.
        # Branching/routing fields (Q1→Q2, Q3→Q3a, Q11a→Q11b, Q12a→Q12b) are
        # handled explicitly in the field loop below.
        _field_opts: Dict[str, List] = {
            'Q1':  [1, 2, 3, 4, 5, 6, 7],
            'Q5':  [1, 2, 3, 4, 5],
            'Q7':  [1, 2, 3],
            'Q8b': [1, 2, 3, 4],
            'Q9':  [1, 2, 4],          # 3 not a valid code
            'Q10': [1, 2, 3, 4],
            'Q17': [1, 2, 3, 4],
            'Q18': [1, 4, 5, 6],       # 2 and 3 not valid codes
            'Q19': [1, 2, 3, 4, 5, 6, 7],
            'Q21': [1, 2, 3, 4],
            'Q24': list(range(1, 13)),
            'Q25': list(range(1, 13)),
        }

        rnd = random.Random(self.seed + 600)

        def mm(choices, p: float = 0.05):
            return rnd.choice(missing_codes) if rnd.random() < p else rnd.choice(choices)

        rows: List[dict] = []
        for puprn in self.puprns:
            _q1 = None
            _q3 = None

            row: dict = {}
            for field in fields:

                # ── PUPRN ─────────────────────────────────────────────────
                if field == 'PUPRN':
                    row[field] = puprn

                # ── Q1 (state) / Q2 routed from Q1 ───────────────────────
                elif field == 'Q1':
                    v = mm([1, 2, 3, 4, 5, 6, 7])
                    row[field] = v; _q1 = v
                elif field == 'Q2':
                    # Q1=6 means "I don't have this" → Q2 code 4 = same
                    row[field] = 4 if _q1 == 6 else mm([1, 2, 3, 4, 5])

                # ── Q3 (state) / Q3a only applicable if Q3=1 ─────────────
                elif field == 'Q3':
                    v = rnd.choice([1, 2])
                    row[field] = v; _q3 = v
                elif field.startswith('Q3a_'):
                    row[field] = rnd.choice([0, 1]) if _q3 == 1 else np.nan

                # ── Q4: outdoor-access checkboxes (always answered) ───────
                elif field.startswith('Q4_'):
                    row[field] = rnd.choice([0, 1])

                # ── Q6: window-opening frequency (non-sequential codes) ───
                elif field.startswith('Q6_'):
                    row[field] = mm([7, 14, 15, 16, 17, 18])

                # ── Q11a: "don't have appliance" flags ────────────────────
                elif field.startswith('Q11a_'):
                    row[field] = rnd.choice([0, 1])

                # ── Q11b: appliance use vs pre-lockdown ───────────────────
                # Skip (NaN) when the household indicated they don't have the
                # appliance via the corresponding Q11a_2_N_1 flag.
                elif field.startswith('Q11b_'):
                    n = field.split('_')[1]
                    row[field] = (np.nan if row.get(f'Q11a_2_{n}_1') == 1
                                  else mm([1, 2, 3, 4]))

                # ── Q12a: "don't have device" flags ───────────────────────
                elif field.startswith('Q12a_'):
                    row[field] = rnd.choice([0, 1])

                # ── Q12b: device use vs pre-lockdown ──────────────────────
                elif field.startswith('Q12b_'):
                    n = field.split('_')[1]
                    row[field] = (np.nan if row.get(f'Q12a_2_{n}_1') == 1
                                  else mm([1, 2, 3, 4]))

                # ── Q22a / Q22b: time-of-day checkboxes (0/1) ────────────
                elif field.startswith('Q22a_') or field.startswith('Q22b_'):
                    row[field] = rnd.choice([0, 1])

                # ── RC1/RC2: research consent; Finished: always answered ──
                elif field in ('RC1', 'RC2'):
                    row[field] = rnd.choice([1, 2])
                elif field == 'Finished':
                    row[field] = rnd.choice([0, 1])

                # ── direct option lookup (with small missing probability) ─
                elif field in _field_opts:
                    row[field] = mm(_field_opts[field])

                else:
                    row[field] = np.nan

            rows.append(row)

        return pd.DataFrame(rows, columns=fields)

    # ---------- Participant summary ----------
    def generate_participant_summary(self) -> pd.DataFrame:
        fields = ['PUPRN', 'Region', 'LSOA', 'grid_cell', 'IMD_quintile']
        # England/Wales regions to choose among when the PUPRN's nation (see
        # self._nation, shared with EPC generation) is "England and Wales" —
        # keeps Region consistent with epcVersion for the same household.
        regions_ew = [
            'NORTH EAST','NORTH WEST','YORKSHIRE AND THE HUMBER','EAST MIDLANDS',
            'WEST MIDLANDS','EAST OF ENGLAND','LONDON','SOUTH EAST','SOUTH WEST',
            'WALES'
        ]
        # LSOA prefix by nation; all other regions are in England (E01)
        _lsoa_prefix = {'WALES': 'W01', 'SCOTLAND': 'S01'}
        data = []
        rnd = random.Random(self.seed + 300)
        for puprn in self.puprns:
            region = 'SCOTLAND' if self._nation[puprn] == 'Scotland' else rnd.choice(regions_ew)
            prefix = _lsoa_prefix.get(region, 'E01')
            lsoa = f"{prefix}{rnd.randint(0, 999999):06d}"
            data.append({
                'PUPRN': puprn,
                'Region': region,
                'LSOA': lsoa,
                'grid_cell': self._grid_cells.get(puprn, ''),
                'IMD_quintile': rnd.randint(1, 5),
            })
        return pd.DataFrame(data, columns=fields)

    # ---------- Follow-up survey ----------
    def generate_follow_up_survey(self) -> pd.DataFrame:  # noqa: C901
        # Field list comes from the data dictionary — mirrors how generate_serl_survey works.
        fields = read_survey_dictionary(self.followup_survey_dictionary_path)

        # ── response-option lists (full text, as in actual SERL data) ─────
        YES_NO_NR  = ['Yes', 'No', 'No response']
        FREQ_6     = ['Always', 'Very often', 'Quite often', 'Not very often',
                      'Never', 'Not applicable, cannot do this']
        CHANGE_6   = ['A lot more', 'A little more', 'About the same',
                      'A little less', 'A lot less', 'Not applicable, cannot do this']
        FREQ_UNOCC = ['Always', 'Very often', 'Quite often', 'Not very often',
                      'Never', 'Not applicable']
        A9_OPTS    = [
            'Yes, some or all have their own source of fuel (e.g., logs, coal, bottled gas etc.)',
            'No, they are all powered by mains gas or electricity',
            'No response',
        ]
        A10_OPTS   = ['Daily', 'Most days', 'Rarely - only if I/we really have to',
                      'Never', 'Varies - depends on temperature or other reasons', "Don't know"]
        A11_OPTS   = ['More often', 'Less often', 'About the same',
                      "I don't have this", 'It is not working', "Don't know"]
        A12_OPTS   = ['A great deal of effort', 'Some effort', 'A little effort',
                      'No effort at all', "Don't know"]
        BATHROOMS  = ['0', '1', '2', '3', '4 or more']
        ADD_REP    = ['Has been added or replaced in the last 12 months', 'No']
        PEOPLE     = ['0 people', '1 person', '2 people', '3 people', '4 or more people']
        INCOMES    = [
            'Below £10,000', '£10,001 to £20,000', '£20,001 to £30,000',
            '£30,001 to £40,000', '£40,001 to £50,000', '£50,001 to £60,000',
            '£60,001 to £70,000', '£70,001 to £80,000', '£80,001 to £90,000',
            '£90,001 to £100,000', 'Above £100,000', 'Prefer not to answer',
        ]
        PAYMENT    = [
            'Direct debit (including online direct debit)',
            'Payment on receipt of bill (by post, telephone, online or at bank/post office)',
            'Pre-payment meter', 'Included in rent', 'Other', "Don't know",
        ]
        C5_OPTS    = ['Very easy', 'Fairly easy', 'Neither easy nor difficult',
                      'Fairly difficult', 'Very difficult', "Don't know"]
        C6_OPTS    = ['Daily', 'Most days', 'Rarely', 'never', "Don't know", 'Prefer not to say']
        WFH        = ['Always work from home', 'Sometimes work from home',
                      'Never work from home', 'Not applicable /prefer not to say']
        E1_OPTS    = ['Living comfortably', 'Doing alright', 'Just about getting by',
                      'Finding it quite difficult', 'Finding it very difficult',
                      "Don't know", 'Prefer not to say']
        MOULD      = ['Minor', 'Substantial', "Don't know"]

        # Fields with a direct option-set lookup (not derivable from name pattern alone).
        # Branching/stateful fields (A8/A9/A10, B5/B6, C3/C4, D1/D2/D3, D5/D6, E2/E3)
        # are handled explicitly in the field loop below.
        _field_opts: Dict[str, List] = {
            'A2': YES_NO_NR, 'A3': YES_NO_NR, 'A4': YES_NO_NR,
            'A7': FREQ_UNOCC, 'A11': A11_OPTS, 'A12': A12_OPTS,
            'B1': BATHROOMS,
            'C1': INCOMES, 'C2_electricity': PAYMENT,
            'C5': C5_OPTS, 'C6': C6_OPTS,
            'D4': WFH,
            'E1': E1_OPTS,
            'Collection_method': ['Online', 'Postal'],
        }

        # B2/B3/B4 sub-field probabilities for *_yes generation
        _B2_PROBS = {1:0.03, 2:0.70, 3:0.05, 4:0.05, 5:0.01, 6:0.02,
                     7:0.03, 8:0.01, 9:0.01, 10:0.01, 11:0.01}
        _B3_PROBS = {2:0.05, 5:0.80, 6:0.70, 7:0.60, 8:0.15, 9:0.03,
                     10:0.80, 11:0.70, 12:0.30, 13:0.40, 14:0.30, 15:0.35, 16:0.05}
        _B4_PROBS = {1:0.70, 2:0.50, 3:0.15, 4:0.20, 5:0.85, 6:0.40, 7:0.05}

        _C4_ANSWERS = {
            'C4_1': 'You feel your home is difficult to heat',
            'C4_2': 'You feel it is difficult to afford the fuel to heat your home',
            'C4_3': 'Prefer not to say',
            'C4_4': 'None of the above',
            'C4_5': 'Other reason',
        }

        _D2_DERIVED  = {'D2_ignored', 'D2_min_total'}
        _D2_FIELDS   = ['D2_0_5', 'D2_6-15', 'D2_16-24', 'D2_24_44',
                        'D2_45-64', 'D2_65_74', 'D2_75_85', 'D2_85plus']
        _D2_WEIGHTS  = [0.05, 0.10, 0.09, 0.30, 0.27, 0.11, 0.06, 0.02]
        _D3_WEIGHTS  = [0.35, 0.20, 0.12, 0.05, 0.08, 0.20]   # D3_1..D3_6
        _D2_OVER16   = ['D2_16-24', 'D2_24_44', 'D2_45-64',
                        'D2_65_74', 'D2_75_85', 'D2_85plus']
        _pcount      = {'0 people': 0, '1 person': 1, '2 people': 2,
                        '3 people': 3, '4 or more people': 4}

        def _spread(total: int, weights: list) -> list:
            """Distribute `total` people across len(weights) buckets by sampling."""
            counts = [0] * len(weights)
            for _ in range(total):
                r = rnd.random(); cum = 0.0
                for i, w in enumerate(weights):
                    cum += w
                    if r < cum:
                        counts[i] += 1; break
                else:
                    counts[-1] += 1
            return counts

        rnd = random.Random(self.seed + 400)

        def nr(choices, p: float = 0.08):
            return np.nan if rnd.random() < p else rnd.choice(choices)

        rows: List[dict] = []
        for puprn in self.puprns:
            has_pv = puprn in self._pv_households
            has_hp = puprn in self._hp_households
            has_ev = puprn in self._ev_households

            # State tracked across fields for branching and derived computations
            _a8 = _a9 = _b5 = _c3 = _c4_chosen = None
            _d1 = None
            _d2_answered = _d3_answered = None
            _d2_vals: dict = {}
            _d3_vals: dict = {}
            _d2_distribution: dict = {}
            _d3_distribution: dict = {}

            row: dict = {}
            for field in fields:

                # ── PUPRN ─────────────────────────────────────────────────
                if field == 'PUPRN':
                    row[field] = puprn

                # ── A1: temperature (internally consistent group) ──────────
                elif field == 'A1_units':
                    v = nr(['Celsius', 'Fahrenheit', "Don't know/can't do this"], p=0.12)
                    row[field] = v
                elif field == 'A1_degC':
                    row[field] = (rnd.randint(15, 25)
                                  if row.get('A1_units') == 'Celsius' else np.nan)
                elif field == 'A1_degF':
                    row[field] = (rnd.randint(59, 77)
                                  if row.get('A1_units') == 'Fahrenheit' else np.nan)
                elif field == 'A1_corr_C':
                    u = row.get('A1_units')
                    if u == 'Celsius':    row[field] = float(row['A1_degC'])
                    elif u == 'Fahrenheit': row[field] = round((row['A1_degF'] - 32) * 5/9, 1)
                    else:                  row[field] = np.nan
                elif field == 'A1_edit':
                    row[field] = (row.get('A1_units') == 'Fahrenheit')
                elif field == 'A1_err':
                    row[field] = (row.get('A1_units') not in ('Celsius', 'Fahrenheit')
                                  and rnd.random() < 0.05)

                # ── A5 frequency / A6 change ──────────────────────────────
                elif field.startswith('A5_'):
                    row[field] = nr(FREQ_6)
                elif field.startswith('A6_'):
                    row[field] = nr(CHANGE_6)

                # ── A8/A9/A10: branching on standalone heaters ────────────
                elif field == 'A8':
                    v = nr(YES_NO_NR); row[field] = v; _a8 = v
                elif field == 'A9':
                    if _a8 == 'No':
                        row[field] = 'NA'
                    elif _a8 == 'Yes':
                        v = nr(A9_OPTS); row[field] = v; _a9 = v
                    else:
                        row[field] = 'No response'
                elif field == 'A10':
                    if _a8 in (None, 'No'):
                        row[field] = 'NA'
                    elif _a9 == 'No, they are all powered by mains gas or electricity':
                        row[field] = 'NA'
                    elif _a9 and _a9 != 'No response':
                        row[field] = nr(A10_OPTS)
                    else:
                        row[field] = 'No response'

                # ── B2_N: heating types (HP trait-aligned) ────────────────
                elif field.startswith('B2_') and field.endswith('_yes'):
                    n = int(field.split('_')[1])
                    if n == 5 and has_hp:    row[field] = 'Yes'
                    elif n == 2 and has_hp:  row[field] = 'No'
                    else: row[field] = 'Yes' if rnd.random() < _B2_PROBS.get(n, 0.02) else 'No'
                elif field.startswith('B2_') and field.endswith('_add_rep'):
                    n = int(field.split('_')[1])
                    row[field] = (rnd.choice(ADD_REP)
                                  if row.get(f'B2_{n}_yes') == 'Yes' else 'NA')
                elif field.startswith('B2_') and field.endswith('_err'):
                    row[field] = rnd.random() < 0.03

                # ── B3_N: technologies (PV/EV trait-aligned) ──────────────
                elif field.startswith('B3_') and field.endswith('_yes'):
                    n = int(field.split('_')[1])
                    if n == 1:    row[field] = 'Yes' if has_pv else 'No'
                    elif n == 3:  row[field] = 'Yes' if (has_pv and rnd.random() < 0.30) else 'No'
                    elif n == 4:  row[field] = 'Yes' if has_ev else 'No'
                    else: row[field] = 'Yes' if rnd.random() < _B3_PROBS.get(n, 0.10) else 'No'
                elif field.startswith('B3_') and field.endswith('_add_rep'):
                    n = int(field.split('_')[1])
                    row[field] = (rnd.choice(ADD_REP)
                                  if row.get(f'B3_{n}_yes') == 'Yes' else 'NA')
                elif field.startswith('B3_') and field.endswith('_err'):
                    row[field] = rnd.random() < 0.03

                # ── B4_N: insulation ──────────────────────────────────────
                elif field.startswith('B4_') and field.endswith('_yes'):
                    n = int(field.split('_')[1])
                    row[field] = 'Yes' if rnd.random() < _B4_PROBS.get(n, 0.20) else 'No'
                elif field.startswith('B4_') and field.endswith('_add_rep'):
                    n = int(field.split('_')[1])
                    row[field] = (rnd.choice(ADD_REP)
                                  if row.get(f'B4_{n}_yes') == 'Yes' else 'NA')
                elif field.startswith('B4_') and field.endswith('_err'):
                    row[field] = rnd.random() < 0.03

                # ── B5 (state) / B6 (branching) ───────────────────────────
                elif field == 'B5':
                    v = nr(['Yes', 'No', "Don't know"]); row[field] = v; _b5 = v
                elif field.startswith('B6_'):
                    if _b5 == 'Yes':                     row[field] = rnd.choice(MOULD)
                    elif _b5 in ('No', "Don't know"):    row[field] = 'NA'
                    else:                                row[field] = 'No response'

                # ── C2_gas: not applicable without a gas supply ───────────
                elif field == 'C2_gas':
                    has_gas = (row.get('B2_2_yes') == 'Yes' or row.get('B2_9_yes') == 'Yes')
                    row[field] = nr(PAYMENT) if has_gas else 'Not applicable / no mains gas'

                # ── C3 (state) / C4 (branching) ───────────────────────────
                elif field == 'C3':
                    v = nr(['Yes', 'No', "Don't know", 'No response'])
                    row[field] = v; _c3 = v
                elif field.startswith('C4_'):
                    if _c3 == 'No':
                        if _c4_chosen is None:
                            _c4_chosen = rnd.choice(list(_C4_ANSWERS))
                        row[field] = (_C4_ANSWERS[field] if field == _c4_chosen else 'No')
                    else:
                        row[field] = 'NA'

                # ── D1 (state) / D1_flag (derived) ───────────────────────
                elif field == 'D1':
                    v = rnd.randint(1, 5) if rnd.random() < 0.92 else np.nan
                    row[field] = v; _d1 = v
                elif field == 'D1_flag':
                    d1_nan = isinstance(_d1, float) and math.isnan(_d1)
                    d2_min = (sum(_pcount.get(v, 0) for v in _d2_vals.values())
                              if _d2_vals else None)
                    row[field] = d1_nan or (
                        d2_min is not None and not d1_nan and d2_min != _d1)

                # ── D2 age groups (all-or-nothing answered) ───────────────
                elif field.startswith('D2_') and field not in _D2_DERIVED:
                    if _d2_answered is None:
                        _d2_answered = rnd.random() < 0.88
                        if _d2_answered:
                            d1_nan = _d1 is None or (isinstance(_d1, float) and math.isnan(_d1))
                            total = rnd.randint(1, 5) if d1_nan else int(_d1)
                            counts = _spread(total, _D2_WEIGHTS)
                            _d2_distribution = dict(zip(_D2_FIELDS, counts))
                    if _d2_answered:
                        v = PEOPLE[min(_d2_distribution.get(field, 0), 4)]
                    else:
                        v = 'No response'
                    row[field] = v; _d2_vals[field] = v
                elif field == 'D2_ignored':
                    row[field] = not bool(_d2_answered)
                elif field == 'D2_min_total':
                    row[field] = (sum(_pcount.get(v, 0) for v in _d2_vals.values())
                                  if _d2_answered else np.nan)

                # ── D3 working situation (all-or-nothing answered) ────────
                elif field.startswith('D3_') and field.split('_')[1].isdigit():
                    if _d3_answered is None:
                        _d3_answered = rnd.random() < 0.88
                        if _d3_answered:
                            if _d2_distribution:
                                over16 = sum(_d2_distribution.get(f, 0) for f in _D2_OVER16)
                            elif _d1 is not None and not (isinstance(_d1, float) and math.isnan(_d1)):
                                over16 = max(1, round(int(_d1) * 0.75))
                            else:
                                over16 = rnd.randint(1, 4)
                            counts = _spread(over16, _D3_WEIGHTS)
                            _d3_distribution = {f'D3_{i+1}': counts[i] for i in range(6)}
                    if _d3_answered:
                        v = PEOPLE[min(_d3_distribution.get(field, 0), 4)]
                    else:
                        v = 'No response'
                    row[field] = v; _d3_vals[field] = v
                elif field in ('D3_flag_high', 'D3_flag_low'):
                    row[field] = False
                elif field == 'D3_ignored':
                    row[field] = not bool(_d3_answered)
                elif field == 'D3_total_over16':
                    row[field] = (
                        sum(_pcount.get(_d3_vals.get(f'D3_{i}', '0 people'), 0)
                            for i in range(1, 7))
                        if _d3_answered else np.nan)

                # ── D4_flag (derived from D3 + D4) ───────────────────────
                elif field == 'D4_flag':
                    if not _d3_answered:
                        row[field] = False
                    else:
                        working = sum(_pcount.get(_d3_vals.get(f'D3_{i}', '0 people'), 0)
                                      for i in (1, 2, 5))
                        d4 = row.get('D4')
                        d4_answered = not (d4 is None or
                                           isinstance(d4, float) and math.isnan(d4))
                        row[field] = (working == 0 and d4_answered)

                # ── D5/D6: EVs (trait-aligned, D6 ≤ D5) ─────────────────
                elif field == 'D5':
                    row[field] = (rnd.choice(['1', '2', '3 or more']) if has_ev
                                  else rnd.choice(['0', "Don't know"]) if rnd.random() < 0.02
                                  else '0')
                elif field == 'D6':
                    d5_n = {'0':0,'1':1,'2':2,'3 or more':3}.get(row.get('D5','0'), 0)
                    row[field] = ('0' if d5_n == 0
                                  else rnd.choice(['0', '1', '2', '3 or more'])
                                  if d5_n >= 3 else rnd.choice(['0'] + [str(i) for i in range(1, d5_n+1)]))
                elif field == 'D6_err':
                    row[field] = False

                # ── E2/E3: numeric satisfaction scales ────────────────────
                elif field == 'E2':
                    row[field] = nr([str(i) for i in range(11)], p=0.08)
                elif field == 'E3':
                    row[field] = nr([str(i) for i in range(10)] + ['11'], p=0.08)

                # ── fields with a direct option lookup ────────────────────
                elif field in _field_opts:
                    p = 0.35 if field == 'C1' else 0.08
                    row[field] = nr(_field_opts[field], p=p)

                # ── fallback: any unrecognised derived flag ────────────────
                else:
                    row[field] = False

            rows.append(row)

        return pd.DataFrame(rows, columns=fields)

    # ---------- Exporters list ----------
    def generate_list_of_exporters(self) -> pd.DataFrame:
        exporters = sorted(self._pv_households)
        return pd.DataFrame(exporters, columns=['PUPRN'])

    # ---------- Write all ----------
    def write_all(self, outfolder: "Union[str, os.PathLike]", mock_only_outfolder: "Optional[Union[str, os.PathLike]]" = None):

        mock_only_dir = Path(mock_only_outfolder) if mock_only_outfolder is not None else Path(outfolder)

        # EPC
        epc_df = self.generate_epc()
        write_csv(epc_df, str(Path(outfolder) / self._fname(self.names.epc)))

        # SERL survey
        serl_df = self.generate_serl_survey()
        write_csv(serl_df, str(Path(outfolder) / self._fname(self.names.survey)))

        # COVID-19 survey
        covid19_df = self.generate_covid19_survey()
        write_csv(covid19_df, str(Path(outfolder) / self._fname(self.names.covid19_survey)))

        # Participant summary
        summary_df = self.generate_participant_summary()
        write_csv(summary_df, str(Path(outfolder) / self._fname(self.names.summary)))

        # Follow-up survey
        followup_df = self.generate_follow_up_survey()
        write_csv(followup_df, str(Path(outfolder) / self._fname(self.names.followup_survey)), encoding="latin-1")

        # Exporters list — mock-only file, not part of the SERL Edition release
        exporters_base = f"{self.names.exporters_prefix}_{self.year}_list_of_exporter_puprns"
        exporters_df = self.generate_list_of_exporters()
        write_csv(exporters_df, str(mock_only_dir / self._fname(exporters_base)))