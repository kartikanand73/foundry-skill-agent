"""Deterministic synthetic order data tied to the three Salesforce accounts.

Tables (all keyed to the Salesforce Account Id so Genie answers line up with the Salesforce skill):
  accounts     one row per Salesforce account (the join key is salesforce_account_id)
  products     catalogue of ~20 products across 4 categories
  orders       ~2,000 orders, Jan 2025 -> yesterday
  order_lines  2-4 lines per order (quantity, list price, discount, net amount)
"""
from __future__ import annotations

import random
from datetime import date, timedelta

ACCOUNTS = [
    # salesforce_account_id, name, industry, region, tier, weight
    ("001fj000026lzjWAAQ", "Acme Corp", "Manufacturing", "West", "Enterprise", 0.45),
    ("001fj000026l3pEAAQ", "Globex Inc", "Technology", "Central", "Enterprise", 0.35),
    ("001fj000026m1nLAAQ", "Initech", "Software", "East", "Mid-Market", 0.20),
]

PRODUCTS = [
    # product_id, name, category, list_price
    ("P-1001", "Industrial Sensor Kit", "Hardware", 1250.00),
    ("P-1002", "Edge Gateway", "Hardware", 2400.00),
    ("P-1003", "Rugged Tablet", "Hardware", 980.00),
    ("P-1004", "Barcode Scanner", "Hardware", 310.00),
    ("P-1005", "Smart Thermostat", "Hardware", 189.00),
    ("P-2001", "Analytics Platform - Annual", "Software", 18000.00),
    ("P-2002", "Field Service App - Seat", "Software", 540.00),
    ("P-2003", "Data Connector Pack", "Software", 3200.00),
    ("P-2004", "Security Add-on", "Software", 4500.00),
    ("P-2005", "Mobile SDK License", "Software", 1500.00),
    ("P-3001", "Implementation Services - Day", "Services", 1800.00),
    ("P-3002", "Training Workshop", "Services", 2500.00),
    ("P-3003", "Premium Support - Annual", "Services", 9600.00),
    ("P-3004", "Health Check Assessment", "Services", 5200.00),
    ("P-4001", "Replacement Battery", "Supplies", 45.00),
    ("P-4002", "Mounting Bracket", "Supplies", 28.00),
    ("P-4003", "Label Roll (case)", "Supplies", 64.00),
    ("P-4004", "Cable Kit", "Supplies", 39.00),
    ("P-4005", "Protective Case", "Supplies", 72.00),
    ("P-4006", "Charging Dock", "Supplies", 129.00),
]

REPS = {"West": ["Maria Lopez", "Dan Whitfield"], "Central": ["Priya Shah", "Tom Becker"],
        "East": ["Kevin Osei", "Laura Kim"]}
CHANNELS = [("Direct", 0.55), ("Partner", 0.25), ("Online", 0.20)]


def _pick(rng: random.Random, pairs):
    r, acc = rng.random(), 0.0
    for v, w in pairs:
        acc += w
        if r <= acc:
            return v
    return pairs[-1][0]


def generate(n_orders: int = 2000, start: date = date(2025, 1, 1), end: date | None = None, seed: int = 2026):
    rng = random.Random(seed)
    end = end or date(2026, 10, 25)
    span = (end - start).days
    accounts = [dict(salesforce_account_id=a, account_name=n, industry=i, region=r, tier=t)
                for a, n, i, r, t, _ in ACCOUNTS]
    products = [dict(product_id=p, product_name=n, category=c, list_price=lp) for p, n, c, lp in PRODUCTS]
    acct_weights = [(a, w) for a, _, _, _, _, w in ACCOUNTS]
    region_of = {a: r for a, _, _, r, _, _ in ACCOUNTS}
    orders, lines = [], []
    days = sorted(rng.randint(0, span) for _ in range(n_orders))
    for i, d in enumerate(days, start=1):
        od = start + timedelta(days=d)
        acct = _pick(rng, acct_weights)
        age = (end - od).days
        if age < 7:
            status = _pick(rng, [("Processing", 0.6), ("Shipped", 0.4)])
        elif age < 21:
            status = _pick(rng, [("Shipped", 0.5), ("Delivered", 0.45), ("Cancelled", 0.05)])
        else:
            status = _pick(rng, [("Delivered", 0.90), ("Cancelled", 0.05), ("Returned", 0.05)])
        oid = f"SO-{100000 + i}"
        total = 0.0
        for ln, prod in enumerate(rng.sample(PRODUCTS, rng.randint(2, 4)), start=1):
            pid, _, cat, lp = prod
            qty = rng.randint(1, 3) if cat in ("Software", "Services") else rng.randint(1, 40)
            disc = rng.choice([0, 0, 0, 5, 10, 15]) + (5 if acct != "001fj000026m1nLAAQ" and qty > 20 else 0)
            net = round(qty * lp * (1 - disc / 100), 2)
            total += net
            lines.append(dict(order_id=oid, line_number=ln, product_id=pid, quantity=qty, unit_price=lp,
                              discount_pct=float(disc), net_amount=net))
        ship = None if status in ("Processing", "Cancelled") else od + timedelta(days=rng.randint(1, 6))
        orders.append(dict(order_id=oid, salesforce_account_id=acct, order_date=od, ship_date=ship, status=status,
                           channel=_pick(rng, CHANNELS), sales_rep=rng.choice(REPS[region_of[acct]]),
                           order_total=round(total, 2), currency="USD"))
    return accounts, products, orders, lines


if __name__ == "__main__":
    a, p, o, l = generate()
    print(len(a), len(p), len(o), len(l), round(sum(x["order_total"] for x in o), 2))
