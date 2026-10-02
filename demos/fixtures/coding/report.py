"""Weekly takings by shop, from the bakery's sales export."""
import csv
from decimal import Decimal


def totals_by_shop(path):
    totals = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            price = Decimal(row["price"].replace("£", ""))
            totals[row["shop"]] = totals.get(row["shop"], Decimal("0")) + int(row["quantity"]) * price
    return totals


def summary(path):
    totals = totals_by_shop(path)
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    lines = [f"{shop}: £{total:.2f}" for shop, total in ranked]
    lines.append(f"Total: £{sum(totals.values()):.2f}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary("sales.csv"))
