"""Build outputs/results.xlsx: usage, rates and bill comparison in one workbook.

Monthly billing quantities (kWh by time-of-use period, heating kWh, therms) are
computed here from the hourly profiles. Every bill in the workbook is an Excel
formula that reads the prices on the Rates sheet, so editing a price there
updates all results.

Run after build_profiles.py. Recalculate the saved file in Excel or LibreOffice
to populate formula values.
"""

import sys
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apply_rates import load_profile, parse_ranges  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
OUT = ROOT / "outputs"

FONT = "Arial"
F_BASE = Font(name=FONT, size=10)
F_BOLD = Font(name=FONT, size=10, bold=True)
F_TITLE = Font(name=FONT, size=14, bold=True)
F_INPUT = Font(name=FONT, size=10, color="0000FF")
F_LINK = Font(name=FONT, size=10, color="008000")
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
FILL_HEAD = PatternFill("solid", fgColor="1F4E78")
FILL_INPUT = PatternFill("solid", fgColor="FFFF00")
FILL_BAND = PatternFill("solid", fgColor="F2F2F2")
THIN = Border(bottom=Side(style="thin", color="BFBFBF"))
WRAP = Alignment(wrap_text=True, vertical="top")

USD = '$#,##0;($#,##0);"-"'
USD2 = '$#,##0.00;($#,##0.00);"-"'
PRICE = '$0.00000'
NUM = '#,##0;(#,##0);"-"'
NUM1 = '#,##0.0;(#,##0.0);"-"'

PLANS = [("standard", "Standard"), ("time_of_use", "Time-of-use"),
         ("space_heating", "Space heating"), ("dual_fuel", "Dual fuel")]
SETUP_LABELS = {
    "gas_furnace": "Gas furnace",
    "ashp_resistance_backup": "Heat pump + electric backup",
    "ashp_gas_backup": "Heat pump + gas backup (dual fuel)",
    "baseboard": "Electric baseboard",
}


def setup_label(scenario_id):
    base = scenario_id.removesuffix("_hpwh")
    return SETUP_LABELS[base] + (" + heat pump water heater" if scenario_id.endswith("_hpwh") else "")


PROFILES = [("average", "Average of 200 sampled homes"), ("median_home", "Median home (ResStock building 164367)")]


def header(ws, row, labels, widths=None):
    for i, label in enumerate(labels, start=1):
        c = ws.cell(row=row, column=i, value=label)
        c.font, c.fill = F_HEAD, FILL_HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    ws.row_dimensions[row].height = 30
    if widths:
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w


def style_body(ws, first_row, last_row, ncols):
    for r in range(first_row, last_row + 1):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=r, column=c)
            color = cell.font.color.rgb if cell.font.color is not None else None
            if not cell.font.b and color not in ("000000FF", "00008000"):
                cell.font = F_BASE
            cell.border = THIN


def monthly_quantities(scenarios, holidays):
    rows = []
    for profile, _ in PROFILES:
        for sc in scenarios.itertuples(index=False):
            df = load_profile(OUT / "profiles" / f"{profile}__{sc.scenario_id}.csv", holidays)
            on_peak = (df["day_type"] == "weekday_nonholiday") & df["hour"].isin(parse_ranges("18-21", False))
            super_off = df["hour"].isin(parse_ranges("0-6", False))
            kwh = df["elec_total_kwh"]
            g = pd.DataFrame({
                "kwh": kwh,
                "heat_kwh": df["elec_heating_kwh"],
                "on_peak": kwh.where(on_peak, 0.0),
                "super_off": kwh.where(super_off, 0.0),
                "base_weekday": kwh.where(~on_peak & ~super_off & (df["day_type"] == "weekday_nonholiday"), 0.0),
                "base_weekend": kwh.where(~super_off & (df["day_type"] == "weekend_holiday"), 0.0),
                "therms": df["gas_total_therms"],
            }).groupby(df["month"]).sum()
            for month, v in g.iterrows():
                rows.append({"profile": profile, "scenario_id": sc.scenario_id, "month": month, **v.to_dict()})
    return pd.DataFrame(rows)


def build_rates_sheet(wb):
    ws = wb.create_sheet("Rates")
    ws["A1"] = "Rate inputs"
    ws["A1"].font = F_TITLE
    ws["A2"] = ("Blue text on yellow = input to replace with the current tariff sheet value. "
                "Every price is unverified (see Read Me). Bills on the other sheets recalculate from these cells.")
    ws["A2"].font = F_BASE
    header_row = 4
    header(ws, header_row, ["Rate", "Component", "Applies to", "Months", "Hours / days", "Price", "Unit", "Source", "Notes"],
           [24, 26, 12, 12, 26, 12, 12, 70, 50])

    cells = {}
    r = header_row + 1
    for rate_file in ["xcel_standard", "xcel_tou", "xcel_space_heating", "xcel_dual_fuel", "centerpoint_residential"]:
        rate = pd.read_csv(CONFIG / "rates" / f"{rate_file}.csv")
        for row in rate.itertuples(index=False):
            hours = "all hours" if row.hours == "0-24" else f"{row.hours} local"
            if row.day_type != "all":
                hours += ", " + row.day_type.replace("_", " ")
            if pd.notna(row.block_min) or pd.notna(row.block_max):
                lo = 0 if pd.isna(row.block_min) else int(row.block_min)
                hours += f"; monthly block from {lo} kWh" + ("" if pd.isna(row.block_max) else f" to {int(row.block_max)} kWh")
            values = [rate_file, row.component, row.load, row.months, hours, row.price, row.unit, row.source,
                      "" if pd.isna(row.notes) else row.notes]
            for c, v in enumerate(values, start=1):
                ws.cell(row=r, column=c, value=v).font = F_BASE
            price = ws.cell(row=r, column=6)
            price.font, price.fill = F_INPUT, FILL_INPUT
            price.number_format = USD2 if row.unit == "USD/month" else PRICE
            cells[(rate_file, row.component)] = f"Rates!$F${r}"
            r += 1
        # Winter block size, taken from the block limits in the rate file.
        blocks = rate["block_max"].dropna()
        if len(blocks):
            values = [rate_file, "winter_block_size", "", "10-12;1-5", "", float(blocks.iloc[0]), "kWh/month",
                      "Block limit in config/rates/" + rate_file + ".csv", "Size of the first winter energy block"]
            for c, v in enumerate(values, start=1):
                ws.cell(row=r, column=c, value=v).font = F_BASE
            ws.cell(row=r, column=6).font, ws.cell(row=r, column=6).fill = F_INPUT, FILL_INPUT
            ws.cell(row=r, column=6).number_format = NUM
            cells[(rate_file, "winter_block_size")] = f"Rates!$F${r}"
            r += 1
    style_body(ws, header_row + 1, r - 1, 9)
    for row in ws.iter_rows(min_row=header_row + 1, max_row=r - 1):
        for cell in row:
            cell.alignment = WRAP
    ws.freeze_panes = ws.cell(row=header_row + 1, column=3)
    return cells


def build_plans_sheet(wb, scenarios, plans):
    ws = wb.create_sheet("Plan Eligibility")
    ws["A1"] = "Which setups each rate plan is modeled for"
    ws["A1"].font = F_TITLE
    ws["A2"] = "Yes/No inputs from config/rate_plans.csv. A setup marked No shows n/a on the Summary sheet."
    ws["A2"].font = F_BASE
    header(ws, 4, ["Setup"] + [label for _, label in PLANS], [34, 16, 16, 16, 16])
    cells = {}
    for i, sc in enumerate(scenarios["scenario_id"], start=5):
        ws.cell(row=i, column=1, value=sc).font = F_BASE
        for j, (plan_id, _) in enumerate(PLANS, start=2):
            elig = plans.set_index("plan_id").loc[plan_id, "eligible_scenarios"]
            ok = elig == "all" or sc in elig.split(";")
            c = ws.cell(row=i, column=j, value="Yes" if ok else "No")
            c.font, c.alignment = F_INPUT, Alignment(horizontal="center")
            cells[(sc, plan_id)] = f"'Plan Eligibility'!${get_column_letter(j)}${i}"
    style_body(ws, 5, 4 + len(scenarios), 5)
    ws.cell(row=6 + len(scenarios), column=1,
            value="Plan descriptions: " + "; ".join(f"{p.plan_id} = {p.description}" for p in plans.itertuples())).font = F_BASE
    return cells


def build_monthly_sheet(wb, q, rc):
    ws = wb.create_sheet("Monthly Bills")
    ws["A1"] = "Monthly billing quantities and bills"
    ws["A1"].font = F_TITLE
    ws["A2"] = ("Columns E-L are model outputs from the hourly profiles (blue). Columns M-Q are formulas using the Rates sheet. "
                "Time-of-use periods use local clock time: on-peak 6-9 p.m. non-holiday weekdays, super off-peak midnight-6 a.m.")
    ws["A2"].font = F_BASE
    hr = 4
    header(ws, hr, ["Profile", "Setup", "Month", "Summer (Jun-Sep)", "Electricity (kWh)", "Heating electricity (kWh)",
                    "Non-heating electricity (kWh)", "On-peak kWh", "Super off-peak kWh", "Base kWh, weekdays", "Base kWh, weekends/holidays",
                    "Gas (therms)",
                    "Standard electric ($)", "Time-of-use electric ($)", "Space heating electric ($)", "Dual fuel electric ($)",
                    "Gas ($)"],
           [12, 30, 8, 10, 13, 13, 13, 12, 12, 12, 12, 12, 13, 13, 13, 13, 12])

    s, t, h, d, g = "xcel_standard", "xcel_tou", "xcel_space_heating", "xcel_dual_fuel", "centerpoint_residential"
    first = hr + 1
    for i, row in enumerate(q.itertuples(index=False)):
        r = first + i
        ws.cell(row=r, column=1, value=row.profile)
        ws.cell(row=r, column=2, value=row.scenario_id)
        ws.cell(row=r, column=3, value=int(row.month))
        ws.cell(row=r, column=4, value=f"=IF(AND(C{r}>=6,C{r}<=9),1,0)")
        for col, key in [(5, "kwh"), (6, "heat_kwh"), (8, "on_peak"), (9, "super_off"), (10, "base_weekday"),
                         (11, "base_weekend"), (12, "therms")]:
            c = ws.cell(row=r, column=col, value=round(float(getattr(row, key)), 3))
            c.font = F_INPUT
        ws.cell(row=r, column=7, value=f"=E{r}-F{r}")

        adders = lambda rate: f"E{r}*({rc[(rate, 'fuel_cost_charge')]}+{rc[(rate, 'resource_adjustment')]})"  # noqa: E731
        blk = rc[(s, "winter_block_size")]
        ws.cell(row=r, column=13, value=(
            f"={rc[(s, 'customer_charge')]}+IF(D{r}=1,E{r}*{rc[(s, 'energy_summer')]},"
            f"MIN(E{r},{blk})*{rc[(s, 'energy_winter_block1')]}+MAX(E{r}-{blk},0)*{rc[(s, 'energy_winter_block2')]})"
            f"+{adders(s)}"))
        ws.cell(row=r, column=14, value=(
            f"={rc[(t, 'customer_charge')]}+H{r}*IF(D{r}=1,{rc[(t, 'on_peak_summer')]},{rc[(t, 'on_peak_winter')]})"
            f"+I{r}*{rc[(t, 'super_off_peak')]}+J{r}*IF(D{r}=1,{rc[(t, 'base_summer_weekday')]},{rc[(t, 'base_winter_weekday')]})"
            f"+K{r}*IF(D{r}=1,{rc[(t, 'base_summer_weekend')]},{rc[(t, 'base_winter_weekend')]})"
            f"+{adders(t)}"))
        ws.cell(row=r, column=15, value=(
            f"={rc[(h, 'customer_charge')]}+IF(D{r}=1,E{r}*{rc[(h, 'energy_summer')]},E{r}*{rc[(h, 'energy_winter_space_heat')]})"
            f"+{adders(h)}"))
        dblk = rc[(d, "winter_block_size")]
        ws.cell(row=r, column=16, value=(
            f"={rc[(d, 'customer_charge')]}+IF(D{r}=1,G{r}*{rc[(d, 'energy_summer')]},"
            f"MIN(G{r},{dblk})*{rc[(d, 'energy_winter_block1')]}+MAX(G{r}-{dblk},0)*{rc[(d, 'energy_winter_block2')]})"
            f"+IF(F{r}>0,{rc[(d, 'ecs_customer_charge')]},0)+F{r}*{rc[(d, 'ecs_energy')]}+{adders(d)}"))
        ws.cell(row=r, column=17, value=(
            f"=IF(L{r}>0,{rc[(g, 'basic_charge')]},0)"
            f"+L{r}*({rc[(g, 'delivery_charge')]}+{rc[(g, 'cost_of_gas')]}+{rc[(g, 'gas_riders')]})"))
    last = first + len(q) - 1
    style_body(ws, first, last, 17)
    for r in range(first, last + 1):
        for c in range(5, 13):
            ws.cell(row=r, column=c).number_format = NUM1
        for c in range(13, 18):
            ws.cell(row=r, column=c).number_format = USD2
    ws.freeze_panes = ws.cell(row=first, column=4)
    ws.auto_filter.ref = f"A{hr}:Q{last}"
    return first, last


def build_summary_sheet(wb, scenarios, elig, mfirst, mlast):
    ws = wb.create_sheet("Summary", 1)
    ws["A1"] = "Annual energy cost by home setup and rate plan"
    ws["A1"].font = F_TITLE
    ws["A2"] = ("Twin Cities metro single-family home, NREL ResStock 2025.1 with 2018 weather. Costs use unverified rates "
                "on the Rates sheet; n/a = plan not modeled for that setup (Plan Eligibility sheet).")
    ws["A2"].font = F_BASE
    widths = [48, 13, 11, 13, 13, 13, 13, 14, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    M = "'Monthly Bills'!"
    rng = lambda col: f"{M}${col}${mfirst}:${col}${mlast}"  # noqa: E731
    elec_col = {"standard": "M", "time_of_use": "N", "space_heating": "O", "dual_fuel": "P"}

    r = 4
    chart_ref = None
    for profile, label in PROFILES:
        ws.cell(row=r, column=1, value=label).font = F_BOLD
        r += 1
        labels = ["Setup", "Electricity (kWh/yr)", "Gas (therms/yr)"] + [f"{p} ($/yr)" for _, p in PLANS] + \
                 ["Lowest cost ($/yr)", "Lowest-cost plan"]
        for i, lab in enumerate(labels, start=1):
            c = ws.cell(row=r, column=i, value=lab)
            c.font, c.fill = F_HEAD, FILL_HEAD
            c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        ws.row_dimensions[r].height = 30
        head_row = r
        r += 1
        top = r
        for sc in scenarios["scenario_id"]:
            crit = f'{rng("A")},"{profile}",{rng("B")},"{sc}"'
            ws.cell(row=r, column=1, value=setup_label(sc)).alignment = WRAP
            ws.cell(row=r, column=2, value=f"=SUMIFS({rng('E')},{crit})").number_format = NUM
            ws.cell(row=r, column=3, value=f"=SUMIFS({rng('L')},{crit})").number_format = NUM
            for j, (plan_id, _) in enumerate(PLANS, start=4):
                c = ws.cell(row=r, column=j, value=(
                    f'=IF({elig[(sc, plan_id)]}="Yes",SUMIFS({rng(elec_col[plan_id])},{crit})+SUMIFS({rng("Q")},{crit}),"n/a")'))
                c.number_format, c.alignment = USD, Alignment(horizontal="right")
            ws.cell(row=r, column=8, value=f"=MIN(D{r}:G{r})").number_format = USD
            ws.cell(row=r, column=9, value=f'=SUBSTITUTE(INDEX($D${head_row}:$G${head_row},MATCH(H{r},D{r}:G{r},0))," ($/yr)","")')
            r += 1
        style_body(ws, top, r - 1, 9)
        for rr in range(top, r):
            for cc in range(2, 10):
                ws.cell(row=rr, column=cc).font = F_BASE
            if (rr - top) % 2:
                for cc in range(1, 10):
                    ws.cell(row=rr, column=cc).fill = FILL_BAND
        if chart_ref is None:
            chart_ref = (head_row, top, r - 1)
        r += 2

    ws.cell(row=r, column=1, value="Electric and gas split, average profile").font = F_BOLD
    r += 1
    split_labels = ["Setup"] + [f"{p} electric ($/yr)" for _, p in PLANS] + ["Gas ($/yr)"]
    for i, lab in enumerate(split_labels, start=1):
        c = ws.cell(row=r, column=i, value=lab)
        c.font, c.fill = F_HEAD, FILL_HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    ws.row_dimensions[r].height = 30
    r += 1
    top = r
    for sc in scenarios["scenario_id"]:
        crit = f'{rng("A")},"average",{rng("B")},"{sc}"'
        ws.cell(row=r, column=1, value=setup_label(sc)).alignment = WRAP
        for j, (plan_id, _) in enumerate(PLANS, start=2):
            c = ws.cell(row=r, column=j, value=(
                f'=IF({elig[(sc, plan_id)]}="Yes",SUMIFS({rng(elec_col[plan_id])},{crit}),"n/a")'))
            c.number_format, c.alignment = USD, Alignment(horizontal="right")
        ws.cell(row=r, column=6, value=f"=SUMIFS({rng('Q')},{crit})").number_format = USD
        r += 1
    style_body(ws, top, r - 1, 6)

    head_row, top, bottom = chart_ref
    chart = BarChart()
    chart.type = "bar"
    chart.title = "Annual cost, average profile ($/yr)"
    chart.y_axis.title = "$ per year"
    chart.y_axis.numFmt = "$#,##0"
    data = Reference(ws, min_col=4, max_col=7, min_row=head_row, max_row=bottom)
    cats = Reference(ws, min_col=1, min_row=top, max_row=bottom)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.height, chart.width = 12, 22
    ws.add_chart(chart, "K4")
    ws.freeze_panes = "B4"


def build_usage_sheet(wb):
    ws = wb.create_sheet("Annual Usage")
    ws["A1"] = "Annual use by end use and highest hourly use"
    ws["A1"].font = F_TITLE
    ws["A2"] = ("Model outputs from outputs/annual_usage.csv. Highest hourly kWh equals average kW over that hour; "
                "the average profile smooths peaks across 200 homes.")
    ws["A2"].font = F_BASE
    a = pd.read_csv(OUT / "annual_usage.csv")
    cols = [("profile", "Profile"), ("scenario_id", "Setup"), ("elec_kwh", "Electricity (kWh)"),
            ("elec_heating_kwh", "Heating (kWh)"), ("elec_cooling_kwh", "Cooling (kWh)"),
            ("elec_hot_water_kwh", "Hot water (kWh)"), ("elec_other_kwh", "Other (kWh)"),
            ("elec_peak_kw", "Highest hour (kWh)"), ("elec_peak_hour_standard", "Highest hour (standard time)"),
            ("gas_therms", "Gas (therms)"), ("gas_heating_therms", "Gas heating (therms)"),
            ("gas_hot_water_therms", "Gas hot water (therms)"), ("gas_other_therms", "Gas other (therms)")]
    header(ws, 4, [c[1] for c in cols], [12, 30, 13, 12, 12, 12, 12, 12, 20, 12, 12, 12, 12])
    for i, row in enumerate(a.itertuples(index=False), start=5):
        for j, (key, _) in enumerate(cols, start=1):
            v = getattr(row, key)
            c = ws.cell(row=i, column=j, value=v)
            c.font = F_INPUT if j > 2 else F_BASE
            if isinstance(v, float):
                c.number_format = NUM1 if key == "elec_peak_kw" else NUM
    style_body(ws, 5, 4 + len(a), len(cols))
    ws.freeze_panes = "C5"

    s = pd.read_csv(OUT / "sample_check.csv")
    r = 7 + len(a)
    ws.cell(row=r, column=1, value="Sample check: annual values from ResStock metadata").font = F_BOLD
    header_labels = ["Metric", "All 2,443 metro homes: mean", "All metro homes: median", "200-home sample: mean", "Median home"]
    for i, lab in enumerate(header_labels, start=1):
        c = ws.cell(row=r + 1, column=i, value=lab)
        c.font, c.fill = F_HEAD, FILL_HEAD
        c.alignment = Alignment(wrap_text=True, horizontal="center")
    ws.row_dimensions[r + 1].height = 30
    for k, row in enumerate(s.itertuples(index=False), start=r + 2):
        for j, v in enumerate(row, start=1):
            c = ws.cell(row=k, column=j, value=v)
            c.font = F_INPUT if j > 1 else F_BASE
            if j > 1:
                c.number_format = NUM
    style_body(ws, r + 2, r + 1 + len(s), 5)


def build_readme(wb):
    ws = wb.active
    ws.title = "Read Me"
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 110
    ws["A1"] = "Twin Cities home energy model: results"
    ws["A1"].font = F_TITLE
    rows = [
        ("Purpose", "Annual and monthly electricity and natural gas cost for a typical Twin Cities metro single-family "
                    "home under four heating setups, with and without a heat pump water heater, on Xcel Energy and "
                    "CenterPoint Energy residential rates."),
        ("Usage source", "NREL End-Use Load Profiles for the U.S. Building Stock, ResStock 2025 Release 1, actual "
                         "meteorological year 2018 (Open Energy Data Initiative). Scope: 2,443 simulated single-family "
                         "detached homes with gas furnaces in Anoka, Carver, Dakota, Hennepin, Ramsey, Scott and "
                         "Washington counties. Gas totals are as NREL published them; the metro mean (1,503 therms/yr) "
                         "is above commonly cited CenterPoint residential averages, a comparison not checked against a "
                         "published source."),
        ("Profiles", "Average = mean of 200 homes sampled in proportion to ResStock weights (seed 2018); smooths hourly "
                     "peaks. Median home = ResStock building 164367 (2,179 sq ft), closest to median floor area, "
                     "electricity and gas use."),
        ("Setups", "Gas furnace (baseline); cold-climate heat pump with electric resistance backup (ResStock upgrade 4); "
                   "dual-fuel heat pump with 95% AFUE gas backup, 35F switchover (upgrade 5); electric baseboard "
                   "(baseline heat delivered at 100% efficiency); each also with a heat pump water heater (upgrade 9). "
                   "Gas cooking, drying and other gas uses remain in every setup."),
        ("Rates: status", "Every price on the Rates sheet is unverified. The build environment could not reach "
                          "xcelenergy.com, centerpointenergy.com, mn.gov or eDockets; values come from web search "
                          "summaries of the sources named in each row, with effective dates not confirmed. Xcel "
                          "Resource Adjustment riders and CenterPoint per-therm riders are set to zero. The Xcel fuel "
                          "cost charge (January 2026) and CenterPoint cost of gas (April 2026) are applied to all months. "
                          "Taxes and franchise fees are excluded. Dual-fuel control events are not modeled."),
        ("Rates: to update", "Replace the yellow cells on the Rates sheet with values from the Xcel Energy Minnesota "
                             "Electric Rate Book (MPUC No. 2, Section 5) and the CenterPoint Energy Minnesota Gas Rate "
                             "Book (Residential Sales Service). All bills recalculate."),
        ("Legend", "Blue text = hardcoded input or model output. Yellow fill = rate input to replace with the tariff "
                   "value. Black text = formula."),
        ("Sheets", "Summary: annual cost by setup and plan, lowest-cost plan, electric/gas split, chart. "
                   "Rates: price inputs. Plan Eligibility: which plans apply to which setups. Monthly Bills: monthly "
                   "kWh, time-of-use period kWh, therms and bill formulas. Annual Usage: end-use totals, highest "
                   "hourly use, sample check."),
        ("Hourly data", "The 8,760-hour profiles are in outputs/profiles/ as CSV files (one per profile and setup)."),
    ]
    for i, (k, v) in enumerate(rows, start=3):
        a, b = ws.cell(row=i, column=1, value=k), ws.cell(row=i, column=2, value=v)
        a.font, b.font = F_BOLD, F_BASE
        a.alignment = b.alignment = WRAP
    ws.cell(row=5, column=2).comment = Comment("Scope and limits are listed in README.md.", "model")


def main():
    scenarios = pd.read_csv(CONFIG / "scenarios.csv")
    plans = pd.read_csv(CONFIG / "rate_plans.csv")
    holidays = pd.to_datetime(pd.read_csv(CONFIG / "holidays.csv")["date"])

    wb = Workbook()
    build_readme(wb)
    rc = build_rates_sheet(wb)
    elig = build_plans_sheet(wb, scenarios, plans)
    q = monthly_quantities(scenarios, holidays)
    mfirst, mlast = build_monthly_sheet(wb, q, rc)
    build_summary_sheet(wb, scenarios, elig, mfirst, mlast)
    build_usage_sheet(wb)
    wb.move_sheet("Monthly Bills", offset=1)

    out = OUT / "results.xlsx"
    wb.save(out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
