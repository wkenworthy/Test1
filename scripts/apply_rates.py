"""Apply the rate plans in config/rate_plans.csv to the hourly profiles.

Each rate file in config/rates/ lists charge components. A component applies to
the hours matching its months, day type and local-clock hours, to one load
(total, heating or non_heating electricity; total gas), and optionally to a
monthly usage block. Monthly charges apply every month of the year for which
the meter has any use.

Outputs:
    outputs/bills_summary.csv    annual cost by profile, setup and rate plan
    outputs/bills_detail.csv     annual units and cost by charge component
    outputs/bills_monthly.csv    monthly electric and gas cost
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
OUT = ROOT / "outputs"


def parse_ranges(spec, inclusive_end):
    """'10-12;1-5' -> set of ints. Months are inclusive; hours are [start, end)."""
    values = set()
    for part in str(spec).split(";"):
        start, end = (int(x) for x in part.split("-"))
        values.update(range(start, end + 1 if inclusive_end else end))
    return values


def load_profile(path, holidays):
    df = pd.read_csv(path)
    local = pd.to_datetime(df["timestamp_local"])
    df["month"] = local.dt.month
    df["hour"] = local.dt.hour
    weekend = local.dt.dayofweek >= 5
    holiday = local.dt.normalize().isin(holidays)
    df["day_type"] = np.where(weekend | holiday, "weekend_holiday", "weekday_nonholiday")
    df["load_electricity_total"] = df["elec_total_kwh"]
    df["load_electricity_heating"] = df["elec_heating_kwh"]
    df["load_electricity_non_heating"] = df["elec_total_kwh"] - df["elec_heating_kwh"]
    df["load_gas_total"] = df["gas_total_therms"]
    return df


def component_cost(df, row, fuel):
    load = df[f"load_{fuel}_{row.load}"]
    mask = df["month"].isin(parse_ranges(row.months, True)) & df["hour"].isin(parse_ranges(row.hours, False))
    if row.day_type != "all":
        mask &= df["day_type"] == row.day_type

    if row.charge_type == "monthly":
        # Monthly charges apply to each month in which this meter has any use.
        by_month = load.groupby(df["month"]).sum()
        months = [m for m in parse_ranges(row.months, True) if by_month.get(m, 0) > 0]
        units = pd.Series(1.0, index=months)
    else:
        units = load.where(mask, 0.0).groupby(df["month"]).sum()
        if pd.notna(row.block_min) or pd.notna(row.block_max):
            lo = 0.0 if pd.isna(row.block_min) else row.block_min
            hi = np.inf if pd.isna(row.block_max) else row.block_max
            units = (units.clip(upper=hi) - lo).clip(lower=0.0)
    return units.reindex(range(1, 13), fill_value=0.0), units.reindex(range(1, 13), fill_value=0.0) * row.price


def bill(df, rate_file, fuel):
    rate = pd.read_csv(CONFIG / "rates" / f"{rate_file}.csv")
    detail, monthly = [], pd.Series(0.0, index=range(1, 13))
    for row in rate.itertuples(index=False):
        units, cost = component_cost(df, row, fuel)
        monthly += cost
        detail.append({"rate": rate_file, "component": row.component, "unit": row.unit,
                       "units": units.sum(), "cost": cost.sum()})
    return detail, monthly


def main():
    plans = pd.read_csv(CONFIG / "rate_plans.csv")
    scenarios = pd.read_csv(CONFIG / "scenarios.csv")["scenario_id"]
    holidays = pd.to_datetime(pd.read_csv(CONFIG / "holidays.csv")["date"])

    summary, details, monthly_rows = [], [], []
    for path in sorted((OUT / "profiles").glob("*.csv")):
        profile, scenario = path.stem.split("__")
        if scenario not in set(scenarios):
            continue
        df = load_profile(path, holidays)
        for plan in plans.itertuples(index=False):
            eligible = plan.eligible_scenarios
            if eligible != "all" and scenario not in eligible.split(";"):
                continue
            keys = {"profile": profile, "scenario_id": scenario, "plan_id": plan.plan_id}
            e_detail, e_month = bill(df, plan.electric_rate, "electricity")
            g_detail, g_month = bill(df, plan.gas_rate, "gas")
            details += [{**keys, **d} for d in e_detail + g_detail]
            monthly_rows += [{**keys, "month": m, "electric_cost": e_month[m], "gas_cost": g_month[m],
                              "total_cost": e_month[m] + g_month[m]} for m in range(1, 13)]
            summary.append({
                **keys,
                "elec_kwh": df["elec_total_kwh"].sum(),
                "gas_therms": df["gas_total_therms"].sum(),
                "electric_cost": e_month.sum(),
                "gas_cost": g_month.sum(),
                "total_cost": e_month.sum() + g_month.sum(),
                "heating_season_cost_oct_may": (e_month + g_month)[[10, 11, 12, 1, 2, 3, 4, 5]].sum(),
            })

    summary = pd.DataFrame(summary).sort_values(["profile", "scenario_id", "plan_id"])
    summary.round(2).to_csv(OUT / "bills_summary.csv", index=False)
    pd.DataFrame(details).round(2).to_csv(OUT / "bills_detail.csv", index=False)
    pd.DataFrame(monthly_rows).round(2).to_csv(OUT / "bills_monthly.csv", index=False)
    with pd.option_context("display.width", 200, "display.max_rows", 100):
        print(summary.round(0).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
