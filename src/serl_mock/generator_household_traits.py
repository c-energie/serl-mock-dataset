"""
Household traits generator — pre-compute device/meter assignments for all households.

Creates a single CSV file listing household traits and meter availability.
Both smart-meter and contextual generators read from this file for perfect alignment.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd

from .ids import select_household_subset
from .utils import seed_random, write_csv, with_edition_suffix


def generate_household_traits(
    puprns: List[str],
    pv_fraction: float,
    hp_fraction: float,
    ev_fraction: float,
    gas_meter_fraction: float,
    export_meter_fraction: float,
    solar_thermal_fraction: float,
    seed: int,
    edition: str = "08",
) -> pd.DataFrame:
    """Generate household traits (PV, HP, EV, solar thermal, meter types) for all households.

    Returns a DataFrame with columns:
    PUPRN, has_pv, has_hp, has_ev, has_solar_thermal, has_gas_meter, has_export_meter (all 0/1).
    """
    seed_random(seed)
    n_households = len(puprns)

    # Compute counts from fractions
    n_pv = int(round(n_households * pv_fraction))
    n_hp = int(round(n_households * hp_fraction))
    n_ev = int(round(n_households * ev_fraction))
    n_gas_meter = int(round(n_households * gas_meter_fraction))
    n_export_meter = int(round(n_households * export_meter_fraction))
    n_solar_thermal = int(round(n_households * solar_thermal_fraction))

    # Select deterministic subsets
    pv_set = select_household_subset(
        puprns=puprns,
        n_selected=n_pv,
        seed=seed,
        seed_offset=600,
        label="pv",
    )
    hp_set = select_household_subset(
        puprns=puprns,
        n_selected=n_hp,
        seed=seed,
        seed_offset=700,
        label="hp",
    )
    ev_set = select_household_subset(
        puprns=puprns,
        n_selected=n_ev,
        seed=seed,
        seed_offset=800,
        label="ev",
    )
    gas_meter_set = select_household_subset(
        puprns=puprns,
        n_selected=n_gas_meter,
        seed=seed,
        seed_offset=900,
        label="gas_meter",
    )
    export_meter_set = select_household_subset(
        puprns=puprns,
        n_selected=n_export_meter,
        seed=seed,
        seed_offset=1000,
        label="export_meter",
    )
    solar_thermal_set = select_household_subset(
        puprns=puprns,
        n_selected=n_solar_thermal,
        seed=seed,
        seed_offset=1100,
        label="solar_thermal",
    )

    # Build DataFrame
    data = []
    for puprn in puprns:
        data.append({
            'PUPRN': puprn,
            'has_pv': 1 if puprn in pv_set else 0,
            'has_hp': 1 if puprn in hp_set else 0,
            'has_ev': 1 if puprn in ev_set else 0,
            'has_solar_thermal': 1 if puprn in solar_thermal_set else 0,
            'has_gas_meter': 1 if puprn in gas_meter_set else 0,
            'has_export_meter': 1 if puprn in export_meter_set else 0,
        })
    
    return pd.DataFrame(data)


def write_household_traits(
    df: pd.DataFrame,
    path: Union[str, os.PathLike],
) -> None:
    """Write household traits DataFrame to CSV."""
    write_csv(df, str(path))


def load_household_traits(
    path: Union[str, os.PathLike],
) -> pd.DataFrame:
    """Load household traits from CSV.
    
    Returns DataFrame indexed by PUPRN with trait columns (0/1).
    """
    df = pd.read_csv(path)
    df.set_index('PUPRN', inplace=True)
    return df
