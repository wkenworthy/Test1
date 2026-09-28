"""Build hourly usage profiles for each home setup in config/scenarios.csv.

Two profiles are produced for each setup:
    average      mean of the sampled homes (data/resstock/sample.csv, in_sample)
    median_home  the single home closest to the population medians

Outputs:
    outputs/profiles/{profile}__{scenario}.csv   8,760 hourly rows
    outputs/annual_usage.csv                     annual totals and peaks by profile and setup
    outputs/sample_check.csv                     sample mean vs. all metro homes, annual totals
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "resstock"
OUT = ROOT / "outputs"

KWH_PER_THERM = 29.3071
KWH_PER_KBTU = 1 / 3.412142

ELEC_HEATING = ["elec_heating", "elec_heating_fans", "elec_heating_backup", "elec_heating_backup_fans"]
ELEC_COOLING = ["elec_cooling", "elec_cooling_fans"]


def load_building(bldg_id, upgrade):
    return pd.read_parquet(DATA / "hourly" / f"u{upgrade}" / f"{bldg_id}.parquet")


def split_end_uses(df):
    """Convert ResStock columns into the end-use breakdown used for billing."""
    out = pd.DataFrame(index=df.index)
    out["elec_heating_kwh"] = df[ELEC_HEATING].sum(axis=1)
    out["elec_cooling_kwh"] = df[ELEC_COOLING].sum(axis=1)
    out["elec_hot_water_kwh"] = df["elec_hot_water"]
    out["elec_other_kwh"] = df["elec_total"] - out[["elec_heating_kwh", "elec_cooling_kwh", "elec_hot_water_kwh"]].sum(axis=1)
    out["gas_heating_therms"] = (df["gas_heating_kwh"] + df["gas_heating_backup_kwh"]) / KWH_PER_THERM
    out["gas_hot_water_therms"] = df["gas_hot_water_kwh"] / KWH_PER_THERM
    out["gas_other_therms"] = (
        df["gas_total_kwh"] - df["gas_heating_kwh"] - df["gas_heating_backup_kwh"] - df["gas_hot_water_kwh"]
    ) / KWH_PER_THERM
    out["heat_delivered_kwh"] = df["heat_delivered_kbtu"] * KWH_PER_KBTU
    return out


def building_scenario(bldg_id, scenario):
    df = split_end_uses(load_building(bldg_id, int(scenario.resstock_upgrade)))

    if scenario.heating_method == "electric_resistance":
        # Baseboard: every unit of heat the baseline furnace delivered is supplied
        # by resistance elements at 100% efficiency; no furnace fan, no gas heat.
        df["elec_heating_kwh"] = df["heat_delivered_kwh"]
        df["gas_heating_therms"] = 0.0

    if pd.notna(scenario.hot_water_upgrade):
        hw = split_end_uses(load_building(bldg_id, int(scenario.hot_water_upgrade)))
        df["elec_hot_water_kwh"] = hw["elec_hot_water_kwh"]
        df["gas_hot_water_therms"] = hw["gas_hot_water_therms"]

    return df.drop(columns="heat_delivered_kwh")


def finalize(df):
    df = df.copy()
    df["elec_total_kwh"] = df[["elec_heating_kwh", "elec_cooling_kwh", "elec_hot_water_kwh", "elec_other_kwh"]].sum(axis=1)
    df["gas_total_therms"] = df[["gas_heating_therms", "gas_hot_water_therms", "gas_other_therms"]].sum(axis=1)
    # ResStock reports local standard time (UTC-6, no daylight saving time).
    # Keep that as the index and add the local clock time used for time-of-use periods.
    std = df.index.tz_localize("Etc/GMT+6")
    df.insert(0, "timestamp_local", std.tz_convert("America/Chicago").strftime("%Y-%m-%d %H:%M"))
    df.index.name = "timestamp_standard"
    cols = ["timestamp_local", "elec_total_kwh", "elec_heating_kwh", "elec_cooling_kwh", "elec_hot_water_kwh",
            "elec_other_kwh", "gas_total_therms", "gas_heating_therms", "gas_hot_water_therms", "gas_other_therms"]
    return df[cols]


def summarize(profile, scenario_id, df):
    return {
        "profile": profile,
        "scenario_id": scenario_id,
        "elec_kwh": df["elec_total_kwh"].sum(),
        "elec_heating_kwh": df["elec_heating_kwh"].sum(),
        "elec_cooling_kwh": df["elec_cooling_kwh"].sum(),
        "elec_hot_water_kwh": df["elec_hot_water_kwh"].sum(),
        "elec_other_kwh": df["elec_other_kwh"].sum(),
        "elec_peak_kw": df["elec_total_kwh"].max(),
        "elec_peak_hour_standard": df["elec_total_kwh"].idxmax(),
        "gas_therms": df["gas_total_therms"].sum(),
        "gas_heating_therms": df["gas_heating_therms"].sum(),
        "gas_hot_water_therms": df["gas_hot_water_therms"].sum(),
        "gas_other_therms": df["gas_other_therms"].sum(),
        "gas_peak_therms_per_hour": df["gas_total_therms"].max(),
    }


def main():
    scenarios = pd.read_csv(ROOT / "config" / "scenarios.csv")
    sample = pd.read_csv(DATA / "sample.csv")
    sample_ids = sample.loc[sample.in_sample, "bldg_id"].tolist()
    median_id = int(sample.loc[sample.median_home, "bldg_id"].iloc[0])

    (OUT / "profiles").mkdir(parents=True, exist_ok=True)
    rows = []
    for sc in scenarios.itertuples(index=False):
        frames = [building_scenario(b, sc) for b in sample_ids]
        profiles = {
            "average": sum(frames) / len(frames),
            "median_home": building_scenario(median_id, sc),
        }
        for name, df in profiles.items():
            df = finalize(df)
            df.round(5).to_csv(OUT / "profiles" / f"{name}__{sc.scenario_id}.csv")
            rows.append(summarize(name, sc.scenario_id, df))
        print(f"built {sc.scenario_id}")

    annual = pd.DataFrame(rows)
    annual["elec_peak_hour_standard"] = annual["elec_peak_hour_standard"].astype(str)
    annual.round(1).to_csv(OUT / "annual_usage.csv", index=False)

    # Check the sample against all metro homes using ResStock's own annual results.
    meta = pd.read_csv(DATA / "metadata.csv")
    cols = {"out.electricity.total.energy_consumption..kwh": "elec_kwh",
            "out.natural_gas.total.energy_consumption..kwh": "gas_kwh", "in.sqft..ft2": "sqft"}
    check = pd.DataFrame({
        "all_metro_homes_mean": meta[list(cols)].mean(),
        "all_metro_homes_median": meta[list(cols)].median(),
        "sample_mean": meta[meta.bldg_id.isin(sample_ids)][list(cols)].mean(),
        "median_home": meta[meta.bldg_id == median_id][list(cols)].iloc[0],
    }).rename(index=cols)
    check.loc["gas_therms"] = check.loc["gas_kwh"] / KWH_PER_THERM
    check.drop(index="gas_kwh").round(1).rename_axis("metric").to_csv(OUT / "sample_check.csv")
    print(check.round(0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
