#!/usr/bin/env python3
"""Pull Rex Concepts metrics from Looker and publish the existing HTML report.

Same run path as Hesburger RO: Chrome cookies for Looker, GitHub API write to
main. The report layout is patched in place. A partial Looker result never
overwrites the live file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import browser_cookie3
import requests
from dateutil.relativedelta import relativedelta
from github import Auth, Github

LOOKER_HOST = "https://bolt.cloud.looker.com"
PUBLIC_QUERY = LOOKER_HOST + "/api/4.0/queries/run/json"
INTERNAL_QUERY = LOOKER_HOST + "/api/internal/core/4.0/queries/run/json"
REPO_NAME = "elenapopa-bot/RexConcepts"
REPORT_PATH = "rex_concepts_report.html"
ENV_FILE = Path.home() / ".rex_concepts_env"
FALLBACK_ENV_FILE = Path.home() / ".hesburger_ro_env"
LOG_FILE = Path.home() / "scripts" / "rex_concepts.log"

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


def log(msg: str) -> None:
    line = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


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


def token_from_file(path: Path) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if line.startswith("GITHUB_TOKEN=") or line.startswith("GH_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def load_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
    if token:
        return token
    token = token_from_file(ENV_FILE) or token_from_file(FALLBACK_ENV_FILE)
    if token:
        return token
    raise RuntimeError(
        f"GITHUB_TOKEN not found in the environment, {ENV_FILE}, or {FALLBACK_ENV_FILE}"
    )


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


def looker_session() -> requests.Session:
    jar = list(browser_cookie3.chrome(domain_name="bolt.cloud.looker.com"))
    session = requests.Session()
    csrf = None
    for cookie in jar:
        session.cookies.set(cookie.name, cookie.value, domain=cookie.domain)
        if cookie.name == "CSRF-TOKEN":
            csrf = cookie.value
    if not csrf:
        raise RuntimeError(
            "No Looker CSRF cookie in Chrome. Log in to bolt.cloud.looker.com and retry."
        )
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
        "X-Requested-With": "XMLHttpRequest",
        "Origin": LOOKER_HOST,
        "Referer": LOOKER_HOST + "/explore/curated_delivery/food_providers_metrics_aggregated",
    })
    log(f"Loaded {len(jar)} Looker cookies from Chrome")
    return session


def run_query(session: requests.Session, endpoint: str | None, body: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    params = {"apply_formatting": "false", "cache": "false"}
    last = None
    urls: list[str] = []
    if endpoint:
        urls.append(endpoint)
    for url in (PUBLIC_QUERY, INTERNAL_QUERY):
        if url not in urls:
            urls.append(url)
    for url in urls:
        response = session.post(url, params=params, json=body, timeout=180)
        if response.status_code == 200 and response.text.lstrip().startswith("["):
            data = response.json()
            log(f"Looker query ok via {url} ({len(data)} rows)")
            return data, url
        snippet = response.text[:240].replace("\n", " ")
        last = f"{url} -> {response.status_code} {snippet}"
        log(f"Looker query not accepted: {last}")
    raise RuntimeError("Looker query failed: " + (last or "no response"))


def fetch_summary(session: requests.Session, end_month: str, endpoint: str | None) -> tuple[list[dict[str, Any]], str]:
    body = {
        "model": "curated_delivery",
        "view": "food_providers_metrics_aggregated",
        "fields": SUMMARY_FIELDS,
        "filters": {
            "food_providers_metrics_aggregated.group_name": "REX CONCEPTS",
            "food_providers_metrics_aggregated.country_name": ", ".join(COUNTRIES),
            "food_providers_metrics_aggregated.report_period_month": f"2025-01-01 to {end_month}-28",
        },
        "sorts": [
            "food_providers_metrics_aggregated.country_name",
            "food_providers_metrics_aggregated.brand_name",
            "food_providers_metrics_aggregated.report_period_month",
        ],
        "limit": "500",
    }
    return run_query(session, endpoint, body)


def fetch_cohorts(
    session: requests.Session, months: list[str], endpoint: str | None
) -> tuple[dict[str, list[dict[str, Any]]], str]:
    results: dict[str, list[dict[str, Any]]] = {}
    for order_month in months:
        body = {
            "model": "curated_delivery",
            "view": "dim_order_delivery__ai",
            "fields": COHORT_FIELDS,
            "filters": {
                "dim_order_delivery_common.order_created_date_local_month": order_month,
                "dim_provider_common.country_name": ", ".join(COUNTRIES),
                "dim_provider_common.group_name": "REX CONCEPTS",
            },
            "limit": "50000",
        }
        rows, endpoint = run_query(session, endpoint, body)
        if not rows:
            raise RuntimeError(f"Cohort query returned no rows for {order_month}")
        results[order_month] = rows
    return results, endpoint or ""


def summary_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        orders = number(value(row, SUMMARY_FIELDS[3]), SUMMARY_FIELDS[3])
        if orders == 0:
            log(f"WARNING: skipping zero-order summary row: {row}")
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


def summary_cells(row: dict[str, Any], cohort_new_users: int) -> list[str]:
    """Format the latest-month summary.

    Looker's ``new_users_count`` can be null even when the underlying cohort
    rows are present. Those rows are already queried for the report's new vs.
    returning charts, so use the current-month new cohort count as the
    equivalent summary value instead of publishing a blank cell.
    """
    reported_new_users = value(row, SUMMARY_FIELDS[4])
    if reported_new_users is None or reported_new_users == "":
        log(
            "Looker returned null new_users_count; using the current-month "
            f"cohort count ({cohort_new_users:,}) for the summary"
        )
        new_users = cohort_new_users
    else:
        new_users = round(number(reported_new_users, SUMMARY_FIELDS[4]))
    return [
        f"{round(number(value(row, SUMMARY_FIELDS[3]), SUMMARY_FIELDS[3])):,}",
        f"{round(number(value(row, SUMMARY_FIELDS[5]), SUMMARY_FIELDS[5]) / 1000)}k €",
        f"{round(number(value(row, SUMMARY_FIELDS[6]), SUMMARY_FIELDS[6]) / 1000)}k €",
        f"{new_users:,}",
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
        re.sub(r"(<td[^>]*>).*?(</td>)", lambda found: f"{found.group(1)}{cell}{found.group(2)}", existing, count=1)
        for existing, cell in zip(existing_cells[:8], cells)
    ]
    updated_cells.append(existing_cells[8])
    replacement = match.group(1) + "".join(updated_cells) + match.group(3)
    section = section[: match.start()] + replacement + section[match.end():]
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
    cohort_counts_by_month: dict[tuple[str, str, str], dict[str, int]],
    end_month: str,
) -> str:
    for name, data in {**arrays, **monthly, **cohorts}.items():
        html = replace_js_const(html, name, js_json(data))
    for (country, brand), row in latest.items():
        html = replace_summary_row(
            html,
            country,
            brand,
            summary_cells(row, cohort_counts_by_month[country, brand, end_month]["new"]),
        )
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


def load_report(token: str) -> str:
    github = Github(auth=Auth.Token(token))
    repo = github.get_repo(REPO_NAME)
    text = repo.get_contents(REPORT_PATH, ref=repo.default_branch).decoded_content.decode("utf-8")
    log(f"Fetched {REPORT_PATH} from {REPO_NAME} ({len(text)} bytes)")
    return text


def publish(token: str, page: str, end_month: str) -> bool:
    github = Github(auth=Auth.Token(token))
    repo = github.get_repo(REPO_NAME)
    contents = repo.get_contents(REPORT_PATH, ref=repo.default_branch)
    current = contents.decoded_content.decode("utf-8")
    if current == page:
        log(f"{REPORT_PATH} already matches this run; no commit created")
        return False
    repo.update_file(
        REPORT_PATH,
        f"Update Rex Concepts report {datetime.now().strftime('%Y-%m-%d')}",
        page,
        contents.sha,
        branch=repo.default_branch,
    )
    log(f"Pushed {REPORT_PATH} through {end_month} to {REPO_NAME} branch {repo.default_branch}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate without publishing")
    args = parser.parse_args()
    log("Rex Concepts update started")
    end_month = last_complete_month()
    months = months_between(START_MONTH, end_month)
    chart_months = months[-11:]
    token = load_token()
    session = looker_session()
    summary_rows, endpoint = fetch_summary(session, end_month, None)
    if not summary_rows:
        raise RuntimeError(f"Summary query returned no rows through {end_month}")
    summary = summary_index(summary_rows)
    require_summary_rows(summary, chart_months)
    cohort_rows, _endpoint = fetch_cohorts(session, months, endpoint)
    cohort_months, retained = cohort_counts(cohort_rows)

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
    latest = {(country, brand): summary[country, brand, end_month] for country, brand, _ in BRANDS}
    updated = patch_html(
        load_report(token), arrays, monthly, cohorts, latest, cohort_months, end_month
    )
    validate(updated, arrays, monthly)
    size_kb = len(updated.encode("utf-8")) / 1024
    if args.dry_run:
        log(f"Dry run: report validated for {end_month}; nothing published")
    else:
        publish(token, updated, end_month)
    log(
        f"SUCCESS Rex Concepts report {end_month}: "
        f"8 sales arrays, 4 cohort brands, 4 monthly series, {size_kb:.1f}KB"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        log("FAILED\n" + traceback.format_exc())
        print(f"ERROR: see {LOG_FILE}", file=sys.stderr)
        raise SystemExit(1)
