"""
Fetch UK bank holidays from the official gov.uk JSON and write
data/reference/uk_bank_holidays_england_wales_scotland.csv.

Run this locally whenever the reference file needs updating:

    uv run python scripts/generate_bank_holidays_csv.py

The output is committed to the repo and read by the mock pipeline.
"""

import json
import csv
import urllib.request
from pathlib import Path

GOV_UK_URL = "https://www.gov.uk/bank-holidays.json"
YEAR_RANGE = range(2019, 2026)

OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "reference"
    / "uk_bank_holidays_england_wales_scotland.csv"
)


def fetch_bank_holidays() -> dict:
    with urllib.request.urlopen(GOV_UK_URL, timeout=15) as resp:
        return json.loads(resp.read().decode())


def build_rows(data: dict) -> list[dict]:
    ew_dates   = {e["date"] for e in data["england-and-wales"]["events"]}
    scot_dates = {e["date"] for e in data["scotland"]["events"]}
    ew_names   = {e["date"]: e["title"] for e in data["england-and-wales"]["events"]}
    scot_names = {e["date"]: e["title"] for e in data["scotland"]["events"]}

    all_dates = sorted(ew_dates | scot_dates)

    rows = []
    for d in all_dates:
        year = int(d[:4])
        if year not in YEAR_RANGE:
            continue
        name = ew_names.get(d) or scot_names.get(d, "")
        rows.append({
            "date":          d,
            "name":          name,
            "england_wales": str(d in ew_dates).lower(),
            "scotland":      str(d in scot_dates).lower(),
        })
    return rows


def main() -> None:
    print(f"Fetching bank holidays from {GOV_UK_URL} ...")
    data = fetch_bank_holidays()
    rows = build_rows(data)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "name", "england_wales", "scotland"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
