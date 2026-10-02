#!/usr/bin/env python3
"""T0.2 — Seed data script (specs/tasks.md T0.2, specs/00-overview.md A-4).

Deterministically generates `orders.csv` for the demo "Revenue Analyst" agent
and prints ground-truth answers used later as eval-case ground truth
(specs/04-feedback-and-evals.md, specs/05-improver.md AC-IM-h hinges on a
clean excl./incl.-refunds distinction in Q3 revenue).

Columns: order_id, created_at, region, product, list_price, amount, status
~2,000 rows; ~8% status=refunded; ~1% missing created_at.

Usage:
    python scripts/seed_demo.py           # generate (or regenerate) orders.csv
    python scripts/seed_demo.py --reset   # same thing; no other state to reset yet (07 §5)

Determinism: a fixed random seed drives every random choice, and rows are
written in a fixed, sorted order, so two runs produce byte-identical files
and byte-identical printed answers.
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 20260102  # fixed seed -> deterministic output across runs
N_ROWS = 2000
REFUND_RATE = 0.08
MISSING_CREATED_AT_RATE = 0.01

YEAR = 2025  # fixed demo year; Q3 = Jul 1 - Sep 30 (calendar quarter)
YEAR_START = datetime(YEAR, 1, 1)
YEAR_END = datetime(YEAR, 12, 31, 23, 59, 59)
Q3_START = datetime(YEAR, 7, 1)
Q3_END = datetime(YEAR, 9, 30, 23, 59, 59)

REGIONS = ["NA", "EMEA", "APAC", "LATAM"]
PRODUCTS = [
    ("Widget", 19.99),
    ("Gadget", 49.99),
    ("Gizmo", 9.99),
    ("Doohickey", 29.99),
    ("Thingamajig", 14.99),
]

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "orders.csv"


def _random_datetime(rng: random.Random, start: datetime, end: datetime) -> datetime:
    delta = end - start
    total_seconds = int(delta.total_seconds())
    offset = rng.randint(0, total_seconds)
    return start + timedelta(seconds=offset)


def generate_rows(seed: int = SEED, n_rows: int = N_ROWS) -> list[dict]:
    """Generate rows deterministically. Pure function of (seed, n_rows)."""
    rng = random.Random(seed)
    rows = []

    for i in range(n_rows):
        order_id = f"o_{i + 1:05d}"
        created_dt = _random_datetime(rng, YEAR_START, YEAR_END)

        region = rng.choice(REGIONS)
        product, list_price = rng.choice(PRODUCTS)

        # amount varies slightly around list_price (e.g. discounts/multi-qty),
        # but stays deterministic given the seed.
        qty = rng.choice([1, 1, 1, 2, 2, 3])
        discount = rng.choice([0, 0, 0, 0.1, 0.2])
        amount = round(list_price * qty * (1 - discount), 2)

        status = "refunded" if rng.random() < REFUND_RATE else "completed"

        missing_created_at = rng.random() < MISSING_CREATED_AT_RATE
        created_at = "" if missing_created_at else created_dt.strftime("%Y-%m-%dT%H:%M:%S")

        rows.append(
            {
                "order_id": order_id,
                "created_at": created_at,
                "region": region,
                "product": product,
                "list_price": f"{list_price:.2f}",
                "amount": f"{amount:.2f}",
                "status": status,
                # kept only in-memory for ground-truth computation; not a CSV column
                "_created_dt": None if missing_created_at else created_dt,
            }
        )

    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    fieldnames = ["order_id", "created_at", "region", "product", "list_price", "amount", "status"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in fieldnames})


def compute_ground_truth(rows: list[dict]) -> dict:
    """Ground-truth answers computable from the generated data.

    Q3 = calendar Q3 of the fixed demo year (Jul 1 - Sep 30), per `created_at`.
    Rows with missing created_at are excluded from any Q3 computation (we
    cannot know their quarter) but are included in the overall row count.
    "Excluding refunds" means status != "refunded"; "including refunds" means
    all rows regardless of status, within the date range.
    """
    q3_rows = [r for r in rows if r["_created_dt"] is not None and Q3_START <= r["_created_dt"] <= Q3_END]

    q3_revenue_incl_refunds = round(sum(float(r["amount"]) for r in q3_rows), 2)
    q3_revenue_excl_refunds = round(
        sum(float(r["amount"]) for r in q3_rows if r["status"] != "refunded"), 2
    )

    revenue_by_product: dict[str, float] = {}
    for r in rows:
        if r["status"] == "refunded":
            continue
        revenue_by_product[r["product"]] = revenue_by_product.get(r["product"], 0.0) + float(r["amount"])

    top3_products = sorted(revenue_by_product.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top3_products = [(name, round(rev, 2)) for name, rev in top3_products]

    n_refunded = sum(1 for r in rows if r["status"] == "refunded")
    n_missing_created_at = sum(1 for r in rows if r["_created_dt"] is None)

    return {
        "row_count": len(rows),
        "refunded_count": n_refunded,
        "refunded_pct": round(100 * n_refunded / len(rows), 2),
        "missing_created_at_count": n_missing_created_at,
        "missing_created_at_pct": round(100 * n_missing_created_at / len(rows), 2),
        "q3_row_count": len(q3_rows),
        "q3_revenue_excl_refunds": q3_revenue_excl_refunds,
        "q3_revenue_incl_refunds": q3_revenue_incl_refunds,
        "top3_products_by_revenue_excl_refunds": top3_products,
    }


def print_ground_truth(gt: dict) -> None:
    print("=== Seed data ground truth (orders.csv) ===")
    print(f"row_count: {gt['row_count']}")
    print(f"refunded_count: {gt['refunded_count']} ({gt['refunded_pct']}%)")
    print(
        f"missing_created_at_count: {gt['missing_created_at_count']} "
        f"({gt['missing_created_at_pct']}%)"
    )
    print(f"q3_row_count (Jul 1 - Sep 30, {YEAR}): {gt['q3_row_count']}")
    print(f"q3_revenue_excl_refunds: {gt['q3_revenue_excl_refunds']:.2f}")
    print(f"q3_revenue_incl_refunds: {gt['q3_revenue_incl_refunds']:.2f}")
    print("top3_products_by_revenue (excl. refunds):")
    for name, rev in gt["top3_products_by_revenue_excl_refunds"]:
        print(f"  {name}: {rev:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Regenerate orders.csv deterministically (no other state to reset at this phase).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help=f"Output path for orders.csv (default: {OUTPUT_PATH})",
    )
    args = parser.parse_args()

    rows = generate_rows()
    write_csv(rows, args.output)
    gt = compute_ground_truth(rows)
    print(f"Wrote {args.output} ({gt['row_count']} rows)")
    print_ground_truth(gt)


if __name__ == "__main__":
    main()
