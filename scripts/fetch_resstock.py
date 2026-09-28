"""Download and filter NREL ResStock data for Twin Cities metro single-family homes.

Source: NREL End-Use Load Profiles for the U.S. Building Stock, ResStock 2025
Release 1 (actual meteorological year 2018), hosted on the Open Energy Data
Initiative (OEDI) data lake. Each building's 15-minute results are reduced to
the end-use columns this project uses and aggregated to hourly values.

Outputs (under data/, not committed):
    data/resstock/metadata.csv            filtered baseline metadata and annual results
    data/resstock/sample.csv              sampled building ids and the median home id
    data/resstock/hourly/u{N}/{bldg}.parquet  hourly end-use values per building and upgrade
"""

import argparse
import io
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "resstock"

BASE_URL = (
    "https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
    "end-use-load-profiles-for-us-building-stock/2025/resstock_amy2018_release_1"
)
STATE = "MN"
METRO_COUNTIES = [
    "Anoka County",
    "Carver County",
    "Dakota County",
    "Hennepin County",
    "Ramsey County",
    "Scott County",
    "Washington County",
]
BUILDING_TYPE = "Single-Family Detached"
HEATING = "Natural Gas Fuel Furnace"

# 15-minute timeseries columns retained, renamed to short names (energy in kWh
# unless noted). Gas is stored in kWh here and converted to therms downstream.
TS_COLUMNS = {
    "out.electricity.total.energy_consumption..kwh": "elec_total",
    "out.electricity.heating.energy_consumption..kwh": "elec_heating",
    "out.electricity.heating_fans_pumps.energy_consumption..kwh": "elec_heating_fans",
    "out.electricity.heating_hp_bkup.energy_consumption..kwh": "elec_heating_backup",
    "out.electricity.heating_hp_bkup_fa.energy_consumption..kwh": "elec_heating_backup_fans",
    "out.electricity.cooling.energy_consumption..kwh": "elec_cooling",
    "out.electricity.cooling_fans_pumps.energy_consumption..kwh": "elec_cooling_fans",
    "out.electricity.hot_water.energy_consumption..kwh": "elec_hot_water",
    "out.natural_gas.total.energy_consumption..kwh": "gas_total_kwh",
    "out.natural_gas.heating.energy_consumption..kwh": "gas_heating_kwh",
    "out.natural_gas.heating_hp_bkup.energy_consumption..kwh": "gas_heating_backup_kwh",
    "out.natural_gas.hot_water.energy_consumption..kwh": "gas_hot_water_kwh",
    "out.load.heating.energy_delivered..kbtu": "heat_delivered_kbtu",
}

META_COLUMNS = [
    "bldg_id",
    "weight",
    "in.county_name",
    "in.sqft..ft2",
    "in.vintage",
    "in.geometry_stories",
    "in.geometry_foundation_type",
    "in.hvac_heating_efficiency",
    "in.hvac_cooling_type",
    "in.water_heater_fuel",
    "in.water_heater_efficiency",
    "out.electricity.total.energy_consumption..kwh",
    "out.natural_gas.total.energy_consumption..kwh",
]


def fetch(url, retries=4):
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2 ** (attempt + 1))


def load_metadata():
    url = f"{BASE_URL}/metadata_and_annual_results/by_state/full/parquet/state={STATE}/{STATE}_upgrade0.parquet"
    meta = pd.read_parquet(io.BytesIO(fetch(url)))
    keep = (
        meta["in.county_name"].isin(METRO_COUNTIES)
        & (meta["in.geometry_building_type_recs"] == BUILDING_TYPE)
        & (meta["in.hvac_heating_type_and_fuel"] == HEATING)
    )
    return meta.loc[keep, META_COLUMNS].reset_index(drop=True)


def pick_median_home(meta):
    """Home closest to the population medians of floor area, electricity and gas use."""
    cols = [
        "in.sqft..ft2",
        "out.electricity.total.energy_consumption..kwh",
        "out.natural_gas.total.energy_consumption..kwh",
    ]
    x = meta[cols].astype(float)
    z = (x - x.median()) / (x.quantile(0.75) - x.quantile(0.25))
    return int(meta.loc[(z**2).sum(axis=1).idxmin(), "bldg_id"])


def to_hourly(raw):
    df = pd.read_parquet(io.BytesIO(raw), columns=["timestamp", *TS_COLUMNS])
    df = df.rename(columns=TS_COLUMNS)
    # ResStock timestamps mark the end of each 15-minute interval in local
    # standard time; shift to interval start so each hour holds its own data.
    df["timestamp"] = (df["timestamp"] - pd.Timedelta(minutes=15)).dt.floor("h")
    return df.groupby("timestamp").sum().astype("float64")


def fetch_building(bldg_id, upgrade):
    out = DATA / "hourly" / f"u{upgrade}" / f"{bldg_id}.parquet"
    if out.exists():
        return
    url = f"{BASE_URL}/timeseries_individual_buildings/by_state/upgrade={upgrade}/state={STATE}/{bldg_id}-{upgrade}.parquet"
    hourly = to_hourly(fetch(url))
    out.parent.mkdir(parents=True, exist_ok=True)
    hourly.to_parquet(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sample-size", type=int, default=200)
    p.add_argument("--seed", type=int, default=2018)
    p.add_argument("--upgrades", default="0,4,5,9", help="ResStock upgrade numbers to download")
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    meta = load_metadata()
    meta.to_csv(DATA / "metadata.csv", index=False)
    print(f"{len(meta)} metro single-family detached gas-furnace homes in ResStock")

    # Sample in proportion to ResStock's sampling weights so the average
    # represents the metro housing stock.
    sample = meta.sample(n=args.sample_size, weights="weight", random_state=args.seed)
    median_id = pick_median_home(meta)
    ids = sorted(set(sample["bldg_id"].astype(int)) | {median_id})
    pd.DataFrame(
        {"bldg_id": ids, "in_sample": [i in set(sample["bldg_id"]) for i in ids], "median_home": [i == median_id for i in ids]}
    ).to_csv(DATA / "sample.csv", index=False)
    print(f"sampled {len(sample)} homes; median home is building {median_id}")

    upgrades = [int(u) for u in args.upgrades.split(",")]
    jobs = [(b, u) for u in upgrades for b in ids]
    done = 0
    with ThreadPoolExecutor(args.workers) as pool:
        for _ in pool.map(lambda j: fetch_building(*j), jobs):
            done += 1
            if done % 50 == 0 or done == len(jobs):
                print(f"  {done}/{len(jobs)} building-upgrade files", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
