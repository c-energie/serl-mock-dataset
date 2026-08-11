# Generation Model — Smart-Meter Data

This page describes how half-hourly electricity and gas values are produced.  The model is deliberately simple but captures the main features of real UK household consumption.

---

## High-level formula

For each household `h` and half-hour period `t`:

```
elec(h, t) ≈ Normal(
    mean  = base_elec(h) × elec_seasonal(doy(t)) × elec_daily(hour(t)),
    noise = mean × variance(h)
) + occasional spike

gas(h, t)  ≈ Normal(
    mean  = base_gas(h) × gas_seasonal(doy(t)) × gas_daily(hour(t)),
    noise = mean × 0.4
)  × has_gas(h)          [heating season]

           | Uniform(50, 300 Wh)   with probability summer_hw_prob
           × has_gas(h)            [non-heating season]
```

The three components — **household profile**, **seasonal multiplier**, **daily multiplier** — are computed independently and multiplied together.  Each can be changed without affecting the others.

---

## Household profiles (`profiles.py`)

One `HouseholdProfile` is drawn per PUPRN at initialisation time using the shared seeded RNG.

| Parameter | What it controls |
|---|---|
| `base_elec_wh` | Baseline electricity consumption per HH period (Wh) at unit multipliers |
| `base_gas_wh` | Baseline gas consumption at peak winter heating demand (Wh) |
| `elec_variance` | Noise level — std = mean × variance (drawn from Uniform[0.4, 0.8]) |
| `has_gas` | Bernoulli draw with probability `profiles.gas_fraction`; currently computed but **not** read by the generator (see below) |

Values are drawn from Gaussian distributions truncated to realistic minima.  The population-level means and standard deviations are set in the `profiles:` section of `serl_mock.yaml`.

> **Gas-meter gating in practice:** `HHSmartMeterGenerator` does not use `HouseholdProfile.has_gas`. The `has_gas(h)` term in the formula above is actually taken from `has_gas_meter` in `mock_internal/household_traits.csv`, which is controlled by `household_traits.gas_meter_fraction` (see [02_configuration.md](02_configuration.md)). The two fractions default to the same value (0.85) but are independent settings.

---

## Seasonal patterns (`patterns.py`)

### Electricity seasonal multiplier
A cosine curve peaking mid-January, troughing mid-July.  Represents increased lighting and supplemental electric heating in winter.

```
elec_seasonal(doy) = 1 + amplitude × cos(2π × (doy − 15) / 365)
```

Default amplitude: **0.3** → ±30 % swing around the annual mean.

### Gas seasonal multiplier
Same cosine, clipped to zero below zero — gas demand is zero in summer (heating off) and rises to ~2× baseline in mid-winter.

```
gas_seasonal(doy) = max(0, cos(2π × (doy − 15) / 365)) × amplitude
```

Default amplitude: **2.0**.

---

## Daily (intraday) patterns (`patterns.py`)

### Electricity daily multiplier

| Time window | Multiplier | Rationale |
|---|---|---|
| 00:00 – 07:00 | 0.5× | Overnight baseload |
| 07:00 – 09:00 | 1.4× | Morning peak (breakfast, lights) |
| 09:00 – 17:00 | 0.9× | Daytime (occupants often away) |
| 17:00 – 21:00 | 1.6× | Evening peak (return home, cooking, TV) |
| 21:00 – 23:00 | 1.2× | Late evening wind-down |

### Gas daily multiplier (heating season only)

| Time window | Multiplier | Rationale |
|---|---|---|
| 22:00 – 06:00 | 0.8× | Setback thermostat overnight |
| 06:00 – 09:00 | 1.5× | Morning warm-up |
| 09:00 – 17:00 | 0.6× | Reduced demand (occupants away) |
| 17:00 – 22:00 | 1.3× | Evening re-heat |

Outside the heating season (`gas_seasonal < gas_heating_threshold`) gas is zero except for occasional hot-water draws.

---

## Noise and spikes

- **Gaussian noise** is added to both electricity and gas.  The standard deviation scales with the local mean so variance is proportional rather than constant.
- **Appliance spikes** are added to electricity with probability `elec_spike_probability` (default 2 %).  When triggered, a uniform draw in `[500 Wh, elec_spike_max_wh]` is added on top.

---

## PV and export generation

- PV households are selected deterministically from the PUPRN list using `household_traits.pv_fraction`.
- Export-meter households are selected deterministically using `household_traits.export_meter_fraction`.
- Only export-meter households receive non-zero electricity export values (`Elec_act_exp_hh_Wh`, `Elec_react_exp_hh_varh`); the export profile follows a simple daylight-shaped solar curve (`sin` over the day, seasonally scaled) rather than the household's own PV trait.
- Non-export-meter households have zero export values.
- Import electricity and gas generation logic is unchanged by PV/export-meter assignment.
- Note: `has_pv` and `has_export_meter` are independent traits — a household can have PV without an export meter (no export values generated) or vice versa. The mock_internal exporter list (`Elec_<year>_list_of_exporter_puprns_editionXX.csv`) is keyed on `has_pv`, not `has_export_meter` — see [02_configuration.md](02_configuration.md).

## Cross-generator consistency caveat

This mock model intentionally prioritises reproducibility and structural validity for testing.
Some cross-dataset interactions are simplified and may not represent all real-world dependencies.

Key links currently enforced consistently include shared PUPRNs, deterministic household traits,
and aligned PV/meter trait usage in the relevant generators.

---

## Edition 07/08 timestamp rules

| Rule | Implementation |
|---|---|
| UTC month cut-off | `pd.date_range` bounded by UTC calendar month start/end |
| Local label (BST/GMT) | `tz_convert("Europe/London")` → `tzname()` |
| Effective date | Midnight UTC rolls back to the previous local date |
| HH index (1–48) | Derived from UTC timestamp; `NA` if not on a 30-min boundary |
| `Valid_read_time` | `True` if HH is not NA |
| Error flags | `-5` invalid time, `-2` very high, `-1` meter max, `1` valid, `0` missing |

A small fraction (~0.05 %) of timestamps is deliberately skewed by 60 seconds to exercise `Valid_read_time = False` and `HH = NA` paths.

---

## How to extend the model

### Change population-level baselines
Edit the `profiles:` section in `config/serl_mock.yaml`.  No code changes needed.

### Change the seasonal or daily curve shape
Edit the relevant function in `src/serl_mock/patterns.py`.  The function signature `(np.ndarray) → np.ndarray` is the only contract.  The generator calls these four functions by name, so renaming requires updating the import in `generator_smartmeter.py`.

### Add a new pattern function (e.g. weekend vs weekday)
1. Add the function to `patterns.py`.
2. Import it in `generator_smartmeter.py`.
3. Compute the new multiplier array (T values) and multiply it into the mean expression in `generate_month`.

### Add a new per-household attribute (e.g. solar panels)
1. Add the field to `HouseholdProfile` in `profiles.py`.
2. Add the draw logic in `generate_profiles`.
3. Cache the array in `HHSmartMeterGenerator.__init__` (like `_has_gas`).
4. Use it in `generate_month`.
