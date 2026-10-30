#!/usr/bin/env python3
"""Update the Rex Concepts monthly report from Looker.

The script intentionally refuses to fill missing data with zeroes: a partial
Looker result must never overwrite an otherwise correct report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import looker_sdk
from dateutil.relativedelta import relativedelta
from looker_sdk import models40 as models

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "rex_concepts_report.html"
CACHE = ROOT / "cache"
START_MONTH = "2025-01"
COUNTRIES = ("Romania", "Czech Republic", "Poland")
BRANDS = (
    ("Romania", "BURGER KING", "RO_BK"),
    ("Romania", "POPEYES", "RO_PLK"),
    ("Czech Republic", "BURGER KING", "CZ_BK"),
    ("Poland", "BURGER KING", "PL_BK"),
)
SUMMARY_FIELDS = [
    "food_providers_metrics_aggregated.report_period_month",
    "food_providers_metrics_aggregated.brand_name",
    "food_providers_metrics_aggregated.country_name",
    "food_providers_metrics_aggregated.number_of_delivered_orders",
    "food_providers_metrics_aggregated.new_users_count",
    "food_providers_metrics_aggregated.provider_price_eur",
    "food_providers_metrics_aggregated.total_sales_after_discount",
    "food_providers_metrics_aggregated.average_order_value",
    "food_providers_metrics_aggregated.provider_avg_rating",
    "food_providers_metrics_aggregated.providers_availability_rate",
    "food_providers_metrics_aggregated.delivered_orders_with_10_min_cooking_delay_from_delivered_orders",
    "food_providers_metrics_aggregated.provider_spent_eur",
    "food_providers_metrics_aggregated.cmp_bolt_spent_eur",
]
COHORT_FIELDS = [
    "dim_provider_common.brand_name",
    "dim_provider_common.country_name",
    "dim_order_delivery_common.first_order_created_same_brand_ts_time",
    "dim_order_delivery_common.active_users_delivery_food_count",
]


def last_complete_month() -> str:
    first = date.today().replace(day=1)
    return (first - relativedelta(months=1)).strftime("%Y-%m")


def months_between(start: str, end: str) -> list[str]:
    current = date.fromisoformat(f"{start}-01")
    final = date.fromisoformat(f"{end}-01")
    months: list[str] = []
    while current <= final:
        months.append(current.strftime("%Y-%m"))
        current += relativedelta(months=1)
    return months


def month_label(month: str) -> str:
    return date.fromisoformat(f"{month}-01").strftime("%b %y")


def js_json(value: Any) -> str:
    """Produce JavaScript JSON while rejecting non-finite numeric values."""
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def configure_looker() -> None:
    required = ("LOOKER_BASE_URL", "LOOKER_CLIENT_ID", "LOOKER_CLIENT_SECRET")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    ini = f"""[Looker]
base_url={os.environ['LOOKER_BASE_URL']}
client_id={os.environ['LOOKER_CLIENT_ID']}
client_secret={os.environ['LOOKER_CLIENT_SECRET']}
verify_ssl=true
"""
    Path.home().joinpath(".looker.ini").write_text(ini, encoding="utf-8")


def value(row: dict[str, Any], field: str) -> Any:
    """Extract values from both Looker JSON cell styles."""
    cell = row.get(field)
    if isinstance(cell, dict):
        return cell.get("value", cell.get("rendered_value"))
    return cell


def number(raw: Any, field: str) -> float:
    if raw is None or raw == "":
        raise ValueError(f"Missing numeric value for {field}")
    if isinstance(raw, str):
        raw = raw.replace(",", "").replace("€", "").replace("%", "").strip()
    return float(raw)


def normalize_month(raw: Any) -> str:
    if raw is None:
        raise ValueError("Missing report period month")
    match = re.search(r"\d{4}-\d{2}", str(raw))
    if not match:
        raise ValueError(f"Could not parse month from {raw!r}")
    return match.group(0)


def cache_json(path: Path, fetch: callable) -> list[dict[str, Any]]:
    CACHE.mkdir(exist_ok=True)
    if path.exists():
        print(f"Using cached {path.relative_to(ROOT)}")
        return json.loads(path.read_text(encoding="utf-8"))
    rows = fetch()
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Cached {len(rows)} rows in {path.relative_to(ROOT)}")
    return rows


def run_query(sdk: Any, query: models.WriteQuery) -> list[dict[str, Any]]:
    created = sdk.create_query(body=query)
    raw = sdk.run_query(query_id=created.id, result_format="json")
    result = json.loads(raw)
    if not isinstance(result, list):
        raise ValueError("Looker returned a non-list query result")
    return result


def fetch_summary(sdk: Any, end_month: str) -> list[dict[str, Any]]:
    path = CACHE / f"summary_{end_month}.json"
    return cache_json(
        path,
        lambda: run_query(
            sdk,
            models.WriteQuery(
                model="curated_delivery",
                view="food_providers_metrics_aggregated",
                fields=SUMMARY_FIELDS,
                filters={
                    "food_providers_metrics_aggregated.group_name": "REX CONCEPTS",
                    "food_providers_metrics_aggregated.country_name": ", ".join(COUNTRIES),
                    "food_providers_metrics_aggregated.report_period_month": (
                        f"2025-01-01 to {end_month}-28"
                    ),
                },
                sorts=[
                    "food_providers_metrics_aggregated.country_name",
                    "food_providers_metrics_aggregated.brand_name",
                    "food_providers_metrics_aggregated.report_period_month",
                ],
                limit="500",
            ),
        ),
    )


def fetch_cohorts(sdk: Any, months: list[str]) -> dict[str, list[dict[str, Any]]]:
    results: dict[str, list[dict[str, Any]]] = {}
    for order_month in months:
        results[order_month] = cache_json(
            CACHE / f"cohort_{order_month}.json",
            lambda order_month=order_month: run_query(
                sdk,
                models.WriteQuery(
                    model="curated_delivery",
                    view="dim_order_delivery__ai",
                    fields=COHORT_FIELDS,
                    filters={
                        "dim_order_delivery_common.order_created_date_local_month": order_month,
                        "dim_provider_common.country_name": ", ".join(COUNTRIES),
                        "dim_provider_common.group_name": "REX CONCEPTS",
                    },
                    limit="50000",
                ),
            ),
        )
    return results


def summary_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        orders = number(value(row, SUMMARY_FIELDS[3]), SUMMARY_FIELDS[3])
        if orders == 0:
            print(f"WARNING: skipping zero-order summary row: {row}", file=sys.stderr)
            continue
        key = (
            str(value(row, SUMMARY_FIELDS[2])),
            str(value(row, SUMMARY_FIELDS[1])),
            normalize_month(value(row, SUMMARY_FIELDS[0])),
        )
        indexed[key] = row
    return indexed


def require_summary_rows(
    index: dict[tuple[str, str, str], dict[str, Any]], months: list[str]
) -> None:
    missing = [
        f"{country}/{brand}/{month}"
        for country, brand, _ in BRANDS
        for month in months
        if (country, brand, month) not in index
    ]
    if missing:
        raise ValueError(
            "Refusing to patch a partial report; missing non-zero summary rows: "
            + ", ".join(missing)
        )


def summary_array(
    index: dict[tuple[str, str, str], dict[str, Any]],
    country: str,
    brand: str,
    field: str,
    months: list[str],
) -> list[int]:
    return [round(number(value(index[country, brand, month], field), field)) for month in months]


def cohort_counts(
    cohort_rows: dict[str, list[dict[str, Any]]],
) -> tuple[dict[tuple[str, str, str], dict[str, int]], dict[tuple[str, str, str, str], int]]:
    monthly: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"new": 0, "ret": 0}
    )
    retained: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for order_month, rows in cohort_rows.items():
        for row in rows:
            country = value(row, COHORT_FIELDS[1])
            brand = value(row, COHORT_FIELDS[0])
            first_order = value(row, COHORT_FIELDS[2])
            if country is None or brand is None or first_order is None:
                continue
            cohort_month = normalize_month(first_order)
            if cohort_month > order_month:
                raise ValueError(
                    f"First order {cohort_month} is after order month {order_month}"
                )
            key = (str(country), str(brand), order_month)
            monthly[key]["new" if cohort_month == order_month else "ret"] += 1
            retained[str(country), str(brand), cohort_month, order_month] += 1
    return monthly, retained


def monthly_object(
    country: str, brand: str, months: list[str], counts: dict[tuple[str, str, str], dict[str, int]]
) -> dict[str, list[Any]]:
    return {
        "labels": [month_label(month) for month in months],
        "new_u": [counts[country, brand, month]["new"] for month in months],
        "ret": [counts[country, brand, month]["ret"] for month in months],
    }


def cohort_array(
    country: str,
    brand: str,
    all_months: list[str],
    end_month: str,
    retained: dict[tuple[str, str, str, str], int],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for cohort_month in all_months:
        cohort_size = retained[country, brand, cohort_month, cohort_month]
        if cohort_size == 0:
            continue
        values: list[float | None] = []
        cohort_date = date.fromisoformat(f"{cohort_month}-01")
        for offset in range(13):
            active_month = (cohort_date + relativedelta(months=offset)).strftime("%Y-%m")
            if active_month > end_month:
                values.append(None)
            elif offset == 0:
                values.append(100)
            else:
                values.append(
                    round(retained[country, brand, cohort_month, active_month] / cohort_size * 100, 1)
                )
        output.append({"c": month_label(cohort_month), "n": cohort_size, "m": values})
    return output


SHORT_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
)
FULL_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
SUMMARY_BRANDS = {
    ("Romania", "BURGER KING"): ("tab-ro", "tab-cz", "🍔 Burger King"),
    ("Romania", "POPEYES"): ("tab-ro", "tab-cz", "🍗 Popeyes"),
    ("Czech Republic", "BURGER KING"): ("tab-cz", "tab-pl", "🍔 Burger King"),
    ("Poland", "BURGER KING"): ("tab-pl", None, "🍔 Burger King"),
}


def js_value_end(html: str, start: int) -> int:
    """Return the index of the closing bracket for the value starting at start."""
    closers = {"[": "]", "{": "}"}
    stack = [closers[html[start]]]
    quote = ""
    index = start + 1
    while index < len(html):
        character = html[index]
        if quote:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = ""
        elif character in ("'", '"', "`"):
            quote = character
        elif character in closers:
            stack.append(closers[character])
        elif character in ("]", "}"):
            if character != stack[-1]:
                raise ValueError(f"Unbalanced JavaScript near offset {index}")
            stack.pop()
            if not stack:
                return index
        index += 1
    raise ValueError("Unterminated JavaScript value")


def replace_js_const(html: str, var_name: str, new_value_str: str) -> str:
    """Replace one const, including nested cohort arrays and spaced declarations."""
    matches = list(re.finditer(rf"const\s+{re.escape(var_name)}\s*=\s*", html))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {var_name} constant, found {len(matches)}")
    match = matches[0]
    if html[match.end()] not in "[{":
        raise ValueError(f"{var_name} is not a JavaScript array or object")
    end = js_value_end(html, match.end()) + 1
    if end < len(html) and html[end] == ";":
        end += 1
    return f"{html[:match.start()]}const {var_name}={new_value_str};{html[end:]}"


def percent_points(raw: Any, field: str) -> float:
    """Accept either a 0–1 ratio or an already-scaled percentage."""
    rendered = number(raw, field)
    return rendered * 100 if abs(rendered) <= 1 else rendered


def summary_cells(row: dict[str, Any]) -> list[str]:
    return [
        f"{round(number(value(row, SUMMARY_FIELDS[3]), SUMMARY_FIELDS[3])):,}",
        f"{round(number(value(row, SUMMARY_FIELDS[5]), SUMMARY_FIELDS[5]) / 1000)}k €",
        f"{round(number(value(row, SUMMARY_FIELDS[6]), SUMMARY_FIELDS[6]) / 1000)}k €",
        f"{round(number(value(row, SUMMARY_FIELDS[4]), SUMMARY_FIELDS[4])):,}",
        f"{number(value(row, SUMMARY_FIELDS[7]), SUMMARY_FIELDS[7]):.2f} €",
        f"{number(value(row, SUMMARY_FIELDS[8]), SUMMARY_FIELDS[8]):.2f} ⭐",
        f"{percent_points(value(row, SUMMARY_FIELDS[9]), SUMMARY_FIELDS[9]):.1f}%",
        f"{percent_points(value(row, SUMMARY_FIELDS[10]), SUMMARY_FIELDS[10]):.1f}%",
    ]


def replace_summary_row(html: str, country: str, brand: str, cells: list[str]) -> str:
    tab_id, next_tab_id, brand_label = SUMMARY_BRANDS[country, brand]
    start = html.find(f'id="{tab_id}"')
    end = html.find(f'id="{next_tab_id}"', start + 1) if next_tab_id else html.find("<script", start)
    if start < 0 or end < 0:
        raise ValueError(f"Could not isolate the {country} report section")
    section = html[start:end]
    pattern = rf"(<tr><td[^>]*>{re.escape(brand_label)}</td>)((?:<td[^>]*>.*?</td>){{9}})(</tr>)"
    matches = list(re.finditer(pattern, section))
    if len(matches) != 1:
        raise ValueError(f"Expected one {country} / {brand} summary row, found {len(matches)}")
    match = matches[0]
    existing_cells = re.findall(r"<td[^>]*>.*?</td>", match.group(2))
    updated_cells = [
        re.sub(r"(<td[^>]*>).*?(</td>)", lambda match: f"{match.group(1)}{cell}{match.group(2)}", existing, count=1)
        for existing, cell in zip(existing_cells[:8], cells)
    ]
    updated_cells.append(existing_cells[8])
    replacement = match.group(1) + "".join(updated_cells) + match.group(3)
    section = section[:match.start()] + replacement + section[match.end():]
    return html[:start] + section + html[end:]


def replace_period_labels(html: str, end_month: str) -> str:
    found = re.search(
        r"Jan 2025 – (" + "|".join(SHORT_MONTHS) + r") (\d{4}) · Preliminary", html
    )
    if not found:
        raise ValueError("Report period subtitle was not found")
    old_short, old_year = found.group(1), found.group(2)
    old_full = FULL_MONTHS[SHORT_MONTHS.index(old_short)]
    end = date.fromisoformat(f"{end_month}-01")
    new_short = SHORT_MONTHS[end.month - 1]
    new_full = FULL_MONTHS[end.month - 1]
    published = end + relativedelta(months=1)
    published_label = f"{FULL_MONTHS[published.month - 1]} {published.year}"
    html = html.replace(f"{old_full} {old_year}", f"{new_full} {end.year}")
    html = html.replace(f"Jan 2025 – {old_short} {old_year}", f"Jan 2025 – {new_short} {end.year}")
    html = html.replace(f"Jan 2025 to {old_short} {old_year}", f"Jan 2025 to {new_short} {end.year}")
    html, count = re.subn(
        r"(Preliminary analysis · )(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{4}",
        rf"\1{published_label}",
        html,
    )
    if count == 0:
        raise ValueError("Report publication date was not found")
    if f"{new_full} {end.year} · Summary" not in html:
        raise ValueError("Summary heading was not updated")
    if f"Jan 2025 – {new_short} {end.year} · Preliminary" not in html:
        raise ValueError("Navigation period was not updated")
    return html


def patch_html(
    html: str,
    arrays: dict[str, list[int]],
    monthly: dict[str, dict[str, list[Any]]],
    cohorts: dict[str, list[dict[str, Any]]],
    latest: dict[tuple[str, str], dict[str, Any]],
    end_month: str,
) -> str:
    for name, data in {**arrays, **monthly, **cohorts}.items():
        html = replace_js_const(html, name, js_json(data))
    for (country, brand), row in latest.items():
        html = replace_summary_row(html, country, brand, summary_cells(row))
    return replace_period_labels(html, end_month)


def validate(
    html: str, arrays: dict[str, list[int]], monthly: dict[str, dict[str, list[Any]]]
) -> None:
    errors: list[str] = []
    for name, data in arrays.items():
        if len(data) != 11:
            errors.append(f"{name}: expected 11 values, got {len(data)}")
    for name, data in monthly.items():
        lengths = {key: len(data[key]) for key in ("labels", "new_u", "ret")}
        if set(lengths.values()) != {11}:
            errors.append(f"{name}: inconsistent or non-11 lengths: {lengths}")
    names = [*arrays, *monthly, *(f"{prefix}_C" for _, _, prefix in BRANDS)]
    for name in names:
        match = re.search(rf"const\s+{name}\s*=\s*([\s\S]*?);", html)
        if not match or re.search(r"\b(undefined|NaN)\b", match.group(1)):
            errors.append(f"{name}: patched data is missing or contains undefined/NaN")
    size = len(html.encode("utf-8"))
    if not 70_000 <= size <= 200_000:
        errors.append(f"HTML size {size} bytes is outside the required 70KB–200KB range")
    if errors:
        raise ValueError("Validation failed:\n - " + "\n - ".join(errors))


def push(end_month: str) -> bool:
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("GH_TOKEN is required to push changes")
    subprocess.run(["git", "config", "user.email", "bot@rexconcepts.com"], cwd=ROOT, check=True)
    subprocess.run(["git", "config", "user.name", "elena-bot"], cwd=ROOT, check=True)
    subprocess.run(["git", "add", "rex_concepts_report.html"], cwd=ROOT, check=True)
    staged = subprocess.run(
        ["git", "diff", "--staged", "--quiet"], cwd=ROOT, check=False
    ).returncode
    if staged == 0:
        print("Report is unchanged; skipping commit and push.")
        return False
    subprocess.run(
        ["git", "commit", "-m", f"Monthly update: {end_month} — auto-generated"],
        cwd=ROOT,
        check=True,
    )
    # Do not persist a credential-bearing remote URL in .git/config.
    remote = f"https://x-access-token:{token}@github.com/elenapopa-bot/RexConcepts.git"
    subprocess.run(["git", "push", remote, "HEAD:main"], cwd=ROOT, check=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate without writing or pushing")
    args = parser.parse_args()
    end_month = last_complete_month()
    months = months_between(START_MONTH, end_month)
    chart_months = months[-11:]
    configure_looker()
    sdk = looker_sdk.init40()
    summary = summary_index(fetch_summary(sdk, end_month))
    require_summary_rows(summary, chart_months)
    cohort_months, retained = cohort_counts(fetch_cohorts(sdk, months))

    array_fields = {
        "SALES_BEFORE_RO_BK_EUR": ("Romania", "BURGER KING", SUMMARY_FIELDS[5]),
        "SALES_AFTER_RO_BK_EUR": ("Romania", "BURGER KING", SUMMARY_FIELDS[6]),
        "SALES_BEFORE_RO_PLK_EUR": ("Romania", "POPEYES", SUMMARY_FIELDS[5]),
        "SALES_AFTER_RO_PLK_EUR": ("Romania", "POPEYES", SUMMARY_FIELDS[6]),
        "SALES_BEFORE_CZ_BK_EUR": ("Czech Republic", "BURGER KING", SUMMARY_FIELDS[5]),
        "SALES_AFTER_CZ_BK_EUR": ("Czech Republic", "BURGER KING", SUMMARY_FIELDS[6]),
        "SALES_BEFORE_PL_BK_EUR": ("Poland", "BURGER KING", SUMMARY_FIELDS[5]),
        "SALES_AFTER_PL_BK_EUR": ("Poland", "BURGER KING", SUMMARY_FIELDS[6]),
        "SPEND_RO_BK": ("Romania", "BURGER KING", SUMMARY_FIELDS[12]),
        "SPEND_RO_PLK": ("Romania", "POPEYES", SUMMARY_FIELDS[12]),
        "SPEND_CZ_BK_FIXED": ("Czech Republic", "BURGER KING", SUMMARY_FIELDS[12]),
        "SPEND_PL_BK_FIXED": ("Poland", "BURGER KING", SUMMARY_FIELDS[12]),
        "PROV_SPEND_RO_BK": ("Romania", "BURGER KING", SUMMARY_FIELDS[11]),
        "PROV_SPEND_RO_PLK": ("Romania", "POPEYES", SUMMARY_FIELDS[11]),
        "PROV_SPEND_CZ_BK": ("Czech Republic", "BURGER KING", SUMMARY_FIELDS[11]),
        "PROV_SPEND_PL_BK": ("Poland", "BURGER KING", SUMMARY_FIELDS[11]),
    }
    arrays = {
        name: summary_array(summary, country, brand, field, chart_months)
        for name, (country, brand, field) in array_fields.items()
    }
    monthly = {
        f"{prefix}_M": monthly_object(country, brand, chart_months, cohort_months)
        for country, brand, prefix in BRANDS
    }
    cohorts = {
        f"{prefix}_C": cohort_array(country, brand, months, end_month, retained)
        for country, brand, prefix in BRANDS
    }
    if not REPORT.exists():
        raise FileNotFoundError(f"Report template not found: {REPORT}")
    latest = {(country, brand): summary[country, brand, end_month] for country, brand, _ in BRANDS}
    updated = patch_html(REPORT.read_text(encoding="utf-8"), arrays, monthly, cohorts, latest, end_month)
    validate(updated, {name: arrays[name] for name in list(arrays)[:8]}, monthly)
    size_kb = len(updated.encode("utf-8")) / 1024
    if args.dry_run:
        changed = updated != REPORT.read_text(encoding="utf-8")
        print(f"Dry run: report {'would change' if changed else 'is unchanged'}; no files or git state changed.")
    else:
        REPORT.write_text(updated, encoding="utf-8")
        push(end_month)
    print(f"""✅ Rex Concepts report updated — {end_month}
   Sales arrays:   8 arrays × 11 months
   Cohort arrays:  4 brands × {len(months)} cohorts
   Monthly arrays: 4 brands × 11 months
   HTML size:      {size_kb:.1f}KB
   Pushed to:      https://github.com/elenapopa-bot/RexConcepts.git""")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
