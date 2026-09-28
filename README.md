# Twin Cities home energy model

Hourly electricity and natural gas use for a typical Twin Cities metro single-family home, with and without electric heat, and annual bills under Xcel Energy and CenterPoint Energy rates.

## Run

```
pip install -r requirements.txt
python scripts/fetch_resstock.py    # downloads ~800 files from NREL (about 5 GB transferred, 290 MB kept in data/)
python scripts/build_profiles.py    # hourly profiles for each setup
python scripts/apply_rates.py       # bills for each setup and rate plan
```

After editing a rate file in `config/rates/`, rerun only `apply_rates.py`.

## Source data

NREL End-Use Load Profiles for the U.S. Building Stock, ResStock 2025 Release 1, actual meteorological year 2018, from the Open Energy Data Initiative data lake (`oedi-data-lake/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2025/resstock_amy2018_release_1`).

- Scope: simulated homes in Anoka, Carver, Dakota, Hennepin, Ramsey, Scott and Washington counties that are single-family detached with a natural gas furnace (2,443 of the 4,971 metro homes in the dataset).
- Weather: actual 2018 weather, not a typical year. 2018 had a cold, late spring in Minnesota.
- Units: ResStock reports gas in kWh; it is converted at 29.3071 kWh per therm.
- Time: ResStock timestamps are local standard time (UTC-6) and mark the end of each 15-minute interval. The profiles are summed to hourly values starting at the hour. `timestamp_local` gives the local clock time with daylight saving time, which is used for time-of-use periods.
- Gas totals are used as NREL published them. The metro mean is 1,503 therms per year, which is above the roughly 800–1,000 therms commonly cited for CenterPoint Minnesota residential customers; that comparison figure has not been checked against a published source.

## Typical home

- `average`: mean hourly use of 200 homes drawn at random (seed 2018) in proportion to ResStock sampling weights. Averaging many homes smooths hourly peaks, so this profile understates a single home's peak demand.
- `median_home`: the one home closest to the population medians of floor area, annual electricity and annual gas (ResStock building 164367, 2,179 sq ft).

`outputs/sample_check.csv` compares the sample with all 2,443 homes.

## Setups (`config/scenarios.csv`)

| Setup | Source |
|---|---|
| `gas_furnace` | ResStock baseline (upgrade 0) |
| `ashp_resistance_backup` | ResStock upgrade 4: cold-climate ducted heat pump, SEER2 17.5, 8.5 HSPF2, electric resistance backup |
| `ashp_gas_backup` | ResStock upgrade 5: dual-fuel heat pump, 95% AFUE gas furnace, 35°F switchover |
| `baseboard` | Baseline home; hourly heat delivered by the furnace is supplied by electric resistance at 100% efficiency, with no furnace fan and no gas heating |
| `*_hpwh` | Each setup with the water heater replaced by the ResStock upgrade 9 heat pump water heater |

Limits of the derived setups:

- The baseboard setup assumes the same heat delivery as the ducted furnace; it does not account for the absence of duct losses or differences in zoning.
- The heat pump water heater variants replace only the hot water end use. The effect of the water heater cooling the surrounding space, which ResStock models in upgrade 9, is not carried into the heating use of the other setups.
- Cooking, clothes drying and other gas uses stay on gas in every setup, so every setup keeps a gas meter and pays the gas basic charge.

## Rates (`config/rates/`, `config/rate_plans.csv`)

Each rate file lists charge components with the months, day type, local-clock hours, load and monthly usage block they apply to. `rate_plans.csv` pairs an electric rate with a gas rate and lists the setups eligible for each plan.

| Plan | Electric rate | Eligible setups |
|---|---|---|
| `standard` | Xcel residential | all |
| `time_of_use` | Xcel residential time-of-use | all |
| `space_heating` | Xcel residential with electric space heating | electric heat pump with resistance backup; baseboard |
| `dual_fuel` | Xcel residential for the house, plus Energy-Controlled Service on a second meter for the heating load | dual-fuel heat pump |

All plans use the CenterPoint Energy residential gas rate.

**Every price in `config/rates/` is marked `verified = no`.** This build environment could not reach xcelenergy.com, centerpointenergy.com, mn.gov or the Minnesota eDockets system. The values come from web search summaries of the rate cards, bill inserts and news reports named in each row's `source` column, and their effective dates are not confirmed. Before relying on the results, replace them with the current tariff sheets:

- Xcel Energy Minnesota Electric Rate Book, MPUC No. 2, Section 5 (residential schedules and Energy-Controlled Service), plus the current Fuel Cost Charge and Resource Adjustment rider values.
- CenterPoint Energy Minnesota Gas Rate Book, Residential Sales Service, plus the monthly cost of gas and per-therm riders.

Values not found and currently set to zero: Xcel Resource Adjustment riders and CenterPoint per-therm riders. The Xcel Fuel Cost Charge uses the January 2026 value for all months, and CenterPoint cost of gas uses the April 2026 value for all months. Taxes and city franchise fees are excluded. Xcel dual-fuel control events are not modeled; the heating load on the Energy-Controlled Service meter is billed as ResStock simulated it.

TOU holidays in `config/holidays.csv` are the six common utility holidays for 2018; confirm them against the Xcel TOU tariff.

## Outputs

| File | Contents |
|---|---|
| `outputs/profiles/{profile}__{setup}.csv` | 8,760 hourly rows: electricity (total, heating, cooling, hot water, other) in kWh and gas (total, heating, hot water, other) in therms |
| `outputs/annual_usage.csv` | Annual totals and the highest hourly use by profile and setup |
| `outputs/bills_summary.csv` | Annual electric, gas and total cost by profile, setup and rate plan, and October–May cost |
| `outputs/bills_detail.csv` | Annual units and cost for each charge component |
| `outputs/bills_monthly.csv` | Monthly electric, gas and total cost |
| `outputs/sample_check.csv` | Sample and median home compared with all metro homes |
