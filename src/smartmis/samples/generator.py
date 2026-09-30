"""DemoMart Retail sample-data generator.

Produces realistic but **entirely fictional** data for a five-branch retail
chain: sales lines, weekly inventory snapshots, operational requests, staff,
branches and holidays. No real customer or employee data is used; staff names
are random combinations of common first and last names.

By default a small, documented share of *data-quality issues* is injected
(missing values, duplicates, invalid dates/numbers, negative quantities,
inconsistent text) plus a few genuine *potential anomalies* (bulk-purchase
spikes and a branch outage day), so every SmartMIS stage has something real to
find. Pass ``inject_issues=False`` for a clean dataset.

The generator is deterministic for a given ``seed``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

COMPANY = "DemoMart Retail"
START_DATE = date(2026, 7, 1)
END_DATE = date(2026, 9, 29)

BRANCHES: tuple[tuple[str, str, str, str, int, float], ...] = (
    # name, code, region, open date, area sqft, relative traffic
    ("Mumbai Central", "MUC", "Mumbai City", "2014-04-01", 42_000, 1.35),
    ("Andheri", "AND", "Mumbai Suburban", "2016-11-15", 36_000, 1.20),
    ("Powai", "POW", "Mumbai Suburban", "2018-06-20", 28_000, 0.95),
    ("Thane", "THN", "Thane District", "2019-09-10", 32_000, 1.00),
    ("Navi Mumbai", "NVM", "Thane District", "2021-02-05", 24_000, 0.80),
)

# category -> [(product name, unit price INR, gross margin %)]
CATALOGUE: dict[str, list[tuple[str, float, float]]] = {
    "Grocery": [
        ("Basmati Rice 5kg", 649, 14),
        ("Sona Masoori Rice 10kg", 799, 12),
        ("Whole Wheat Atta 10kg", 489, 11),
        ("Toor Dal 1kg", 169, 13),
        ("Moong Dal 1kg", 149, 13),
        ("Chana Dal 1kg", 109, 14),
        ("Sugar 1kg", 48, 6),
        ("Iodised Salt 1kg", 28, 18),
        ("Sunflower Oil 1L", 159, 9),
        ("Mustard Oil 1L", 189, 10),
        ("Poha 500g", 45, 20),
        ("Rava 1kg", 62, 19),
        ("Turmeric Powder 200g", 58, 28),
        ("Red Chilli Powder 200g", 72, 27),
        ("Garam Masala 100g", 85, 32),
        ("Groundnuts 500g", 95, 17),
        ("Kabuli Chana 1kg", 139, 15),
        ("Rajma 1kg", 165, 15),
        ("Jaggery 1kg", 79, 16),
        ("Vermicelli 400g", 42, 22),
    ],
    "FMCG": [
        ("Instant Noodles 4-pack", 56, 16),
        ("Tomato Ketchup 1kg", 139, 21),
        ("Corn Flakes 875g", 349, 18),
        ("Oats 1kg", 199, 19),
        ("Peanut Butter 340g", 179, 23),
        ("Mixed Fruit Jam 500g", 149, 22),
        ("Glucose Biscuits 800g", 90, 14),
        ("Cream Biscuits 300g", 60, 17),
        ("Potato Chips 150g", 50, 24),
        ("Namkeen Mix 400g", 99, 25),
        ("Dark Chocolate 100g", 120, 30),
        ("Instant Coffee 100g", 299, 20),
        ("Tea Leaves 500g", 265, 18),
        ("Pasta Penne 500g", 115, 26),
        ("Soya Chunks 200g", 44, 23),
        ("Honey 500g", 249, 24),
        ("Pickle Mango 400g", 129, 29),
        ("Baking Soda 100g", 25, 30),
        ("Custard Powder 100g", 48, 28),
        ("Muesli 500g", 329, 22),
    ],
    "Bakery": [
        ("Sandwich Bread 400g", 45, 22),
        ("Brown Bread 400g", 55, 24),
        ("Multigrain Bread 400g", 65, 26),
        ("Pav 6-pack", 30, 25),
        ("Butter Croissant", 55, 38),
        ("Chocolate Muffin", 60, 40),
        ("Plum Cake 300g", 180, 34),
        ("Garlic Bread", 95, 36),
        ("Rusk 300g", 70, 27),
        ("Fruit Bun", 25, 33),
        ("Cheese Danish", 85, 39),
        ("Burger Buns 4-pack", 50, 28),
        ("Whole Wheat Cookies 250g", 110, 35),
        ("Banana Walnut Loaf", 220, 37),
        ("Cinnamon Roll", 75, 41),
        ("Pizza Base 2-pack", 60, 30),
    ],
    "Beverages": [
        ("Mineral Water 1L", 20, 25),
        ("Orange Juice 1L", 125, 21),
        ("Mango Drink 1.2L", 90, 22),
        ("Cola 2L", 95, 18),
        ("Lemon Soda 750ml", 40, 23),
        ("Coconut Water 200ml", 45, 20),
        ("Energy Drink 250ml", 115, 26),
        ("Cold Coffee 200ml", 50, 24),
        ("Buttermilk 500ml", 30, 19),
        ("Flavoured Milk 200ml", 35, 18),
        ("Green Tea 25 bags", 160, 29),
        ("Apple Juice 1L", 135, 21),
        ("Soda Water 750ml", 25, 27),
        ("Iced Tea 500ml", 60, 25),
    ],
    "Personal Care": [
        ("Herbal Shampoo 340ml", 245, 27),
        ("Anti-dandruff Shampoo 180ml", 199, 28),
        ("Bath Soap 4-pack", 180, 22),
        ("Toothpaste 150g", 99, 24),
        ("Toothbrush Soft", 40, 35),
        ("Face Wash 100ml", 175, 33),
        ("Body Lotion 400ml", 299, 31),
        ("Coconut Hair Oil 500ml", 189, 23),
        ("Hand Wash Refill 750ml", 115, 29),
        ("Deodorant 150ml", 225, 34),
        ("Talcum Powder 300g", 160, 26),
        ("Shaving Foam 200g", 190, 30),
        ("Sanitary Pads 20s", 180, 21),
        ("Lip Balm 4.5g", 95, 40),
        ("Sunscreen SPF50 50ml", 349, 36),
        ("Cotton Buds 100s", 55, 38),
    ],
    "Household": [
        ("Detergent Powder 4kg", 499, 17),
        ("Liquid Detergent 2L", 379, 19),
        ("Dishwash Bar 3-pack", 60, 21),
        ("Dishwash Gel 750ml", 145, 24),
        ("Floor Cleaner 1L", 179, 25),
        ("Toilet Cleaner 1L", 155, 24),
        ("Glass Cleaner 500ml", 110, 28),
        ("Garbage Bags 30s", 120, 32),
        ("Kitchen Towels 2-roll", 150, 27),
        ("Toilet Rolls 6-pack", 220, 23),
        ("Aluminium Foil 9m", 99, 26),
        ("Scrub Pads 3-pack", 50, 34),
        ("Mosquito Repellent Refill", 85, 29),
        ("Air Freshener 240ml", 199, 33),
        ("Phenyl 1L", 90, 30),
        ("LED Bulb 9W", 110, 35),
    ],
}

DEPARTMENTS = ("Store Operations", "Maintenance", "IT Support", "Merchandising", "Finance", "HR")
REQUEST_TYPES: dict[str, tuple[str, ...]] = {
    "Store Operations": ("Stock Transfer", "Shelf Replenishment", "Customer Complaint"),
    "Maintenance": ("Equipment Repair", "Refrigeration Fault", "Facility Issue"),
    "IT Support": ("POS Issue", "Network Issue", "Access Request"),
    "Merchandising": ("Price Change", "Promotion Setup", "Planogram Update"),
    "Finance": ("Purchase Request", "Vendor Payment Query", "Petty Cash Claim"),
    "HR": ("Leave Query", "Onboarding", "Payroll Query"),
}
VALUE_REQUEST_TYPES = {"Stock Transfer", "Purchase Request", "Equipment Repair", "Petty Cash Claim"}
PRIORITIES = ("critical", "high", "medium", "low")
PRIORITY_WEIGHTS = (0.08, 0.22, 0.45, 0.25)
SLA_HOURS = {"critical": 2, "high": 4, "medium": 8, "low": 24}

FIRST_NAMES = (
    "Aarav",
    "Aditi",
    "Akash",
    "Ananya",
    "Arjun",
    "Bhavna",
    "Chirag",
    "Deepa",
    "Farhan",
    "Gauri",
    "Harsh",
    "Ishita",
    "Jatin",
    "Kavya",
    "Kunal",
    "Lata",
    "Manav",
    "Meera",
    "Nikhil",
    "Neha",
    "Omkar",
    "Pooja",
    "Pranav",
    "Riya",
    "Rohan",
    "Sanya",
    "Sameer",
    "Tanvi",
    "Uday",
    "Vidya",
    "Varun",
    "Yash",
    "Zoya",
    "Kiran",
    "Suresh",
    "Rekha",
    "Imran",
    "Sneha",
    "Tushar",
    "Asha",
)
LAST_NAMES = (
    "Sharma",
    "Patil",
    "Iyer",
    "Deshmukh",
    "Khan",
    "Nair",
    "Joshi",
    "Kulkarni",
    "Mehta",
    "Rao",
    "Shah",
    "Pillai",
    "Gupta",
    "Pawar",
    "Fernandes",
    "Chavan",
    "Menon",
    "Verma",
    "Shinde",
    "Bhatt",
)
DESIGNATIONS = ("Associate", "Senior Associate", "Supervisor", "Assistant Manager", "Manager")

HOLIDAYS: tuple[tuple[str, str], ...] = (
    ("2026-01-26", "Republic Day"),
    ("2026-05-01", "Maharashtra Day"),
    ("2026-08-15", "Independence Day"),
    ("2026-09-14", "DemoMart Annual Stock Take"),
    ("2026-10-02", "Gandhi Jayanti"),
    ("2026-12-25", "Christmas"),
)


@dataclass
class SampleDataset:
    """All generated tables plus notes on what was injected."""

    sales: pd.DataFrame
    inventory: pd.DataFrame
    orders: pd.DataFrame
    employees: pd.DataFrame
    branches: pd.DataFrame
    holidays: pd.DataFrame
    injected: dict[str, int] = field(default_factory=dict)

    def tables(self) -> dict[str, pd.DataFrame]:
        return {
            "sales": self.sales,
            "inventory": self.inventory,
            "orders": self.orders,
            "employees": self.employees,
            "branches": self.branches,
            "holidays": self.holidays,
        }

    def write(self, output_dir: str | Path, *, excel: bool = True) -> list[Path]:
        """Write CSVs (and a multi-sheet workbook) to ``output_dir``."""
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for name, frame in self.tables().items():
            path = directory / f"{name}.csv"
            frame.to_csv(path, index=False)
            written.append(path)
        if excel:
            written.append(self._write_workbook(directory / "demomart_mis_sample.xlsx"))
        return written

    def _write_workbook(self, path: Path) -> Path:
        latest = self.inventory["snapshot_date"].max()
        extract = self.sales.head(500)
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            self.sales.to_excel(writer, sheet_name="Sales", index=False)
            self.inventory[self.inventory["snapshot_date"] == latest].to_excel(
                writer, sheet_name="Inventory", index=False
            )
            self.orders.to_excel(writer, sheet_name="Orders", index=False)
            # A report-style sheet with title rows above the header, to demo header detection.
            extract.to_excel(writer, sheet_name="Sales_Extract", index=False, startrow=3)
            sheet = writer.sheets["Sales_Extract"]
            sheet["A1"] = f"{COMPANY} — Daily Sales Extract"
            sheet["A2"] = f"Generated {datetime(2026, 9, 30, 8, 30):%d %b %Y %H:%M}"
        return path


class SampleDataGenerator:
    """Builds a :class:`SampleDataset`.

    Args:
        seed: Random seed for reproducibility.
        scale: Multiplier on transaction volume (``scale=20`` ≈ 500k sales lines,
            useful for performance testing).
        inject_issues: Add documented data-quality issues.
    """

    def __init__(self, seed: int = 42, *, scale: float = 1.0, inject_issues: bool = True) -> None:
        if scale <= 0:
            raise ValueError("scale must be positive")
        self.rng = np.random.default_rng(seed)
        self.scale = scale
        self.inject_issues = inject_issues
        self.days = pd.date_range(START_DATE, END_DATE, freq="D")
        self.injected: dict[str, int] = {}

    # ------------------------------------------------------------------ public
    def generate(self) -> SampleDataset:
        products = self._products()
        branches = self._branches()
        sales_clean = self._sales(products)
        inventory = self._inventory(products, sales_clean)
        employees = self._employees()
        orders = self._orders(employees)
        sales = self._inject_sales_issues(sales_clean) if self.inject_issues else sales_clean
        if self.inject_issues:
            orders = self._inject_order_issues(orders)
        holidays = pd.DataFrame(HOLIDAYS, columns=["holiday_date", "holiday_name"])
        return SampleDataset(
            sales=sales,
            inventory=inventory,
            orders=orders,
            employees=employees,
            branches=branches,
            holidays=holidays,
            injected=dict(self.injected),
        )

    # ---------------------------------------------------------------- masters
    def _products(self) -> pd.DataFrame:
        rows = []
        for c_index, (category, items) in enumerate(CATALOGUE.items(), start=1):
            for p_index, (name, price, margin) in enumerate(items, start=1):
                rows.append(
                    {
                        "sku": f"SKU{c_index}{p_index:03d}",
                        "product_name": name,
                        "category": category,
                        "unit_price": float(price),
                        "cost_price": round(price * (1 - margin / 100), 2),
                    }
                )
        products = pd.DataFrame(rows)
        # Zipf-like popularity; a handful of near-dormant SKUs become slow movers.
        ranks = self.rng.permutation(len(products)) + 1
        popularity = 1 / ranks**0.9
        dormant = self.rng.choice(len(products), size=6, replace=False)
        popularity[dormant] *= 0.01
        products["popularity"] = popularity / popularity.sum()
        return products

    @staticmethod
    def _branches() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"branch": n, "branch_code": c, "region": r, "open_date": o, "area_sqft": a}
                for n, c, r, o, a, _ in BRANCHES
            ]
        )

    def _employees(self, count: int = 240) -> pd.DataFrame:
        rng = self.rng
        first = rng.choice(FIRST_NAMES, size=count)
        last = rng.choice(LAST_NAMES, size=count)
        branch_names = [b[0] for b in BRANCHES]
        join_offsets = rng.integers(0, 365 * 11, size=count)
        join_dates = pd.Timestamp("2026-06-30") - pd.to_timedelta(join_offsets, unit="D")
        return pd.DataFrame(
            {
                "employee_id": [f"DM{1001 + i}" for i in range(count)],
                "name": [f"{f} {s}" for f, s in zip(first, last, strict=True)],
                "branch": rng.choice(branch_names, size=count),
                "department": rng.choice(
                    DEPARTMENTS, size=count, p=(0.45, 0.12, 0.1, 0.15, 0.1, 0.08)
                ),
                "designation": rng.choice(
                    DESIGNATIONS, size=count, p=(0.45, 0.25, 0.15, 0.1, 0.05)
                ),
                "join_date": join_dates.strftime("%Y-%m-%d"),
            }
        )

    # ------------------------------------------------------------------- sales
    def _sales(self, products: pd.DataFrame) -> pd.DataFrame:
        rng = self.rng
        n_days = len(self.days)
        weekday = self.days.dayofweek.to_numpy()
        day_factor = np.select([weekday == 5, weekday == 6], [1.30, 1.40], default=1.0)
        trend = np.linspace(0.95, 1.08, n_days)

        branch_grid, day_grid = np.meshgrid(
            np.arange(len(BRANCHES)), np.arange(n_days), indexing="ij"
        )
        branch_idx, day_idx = branch_grid.ravel(), day_grid.ravel()
        traffic = np.array([b[5] for b in BRANCHES])[branch_idx]
        expected = 22 * self.scale * traffic * day_factor[day_idx] * trend[day_idx]

        # Potential anomaly: Thane had a power outage on 20 Aug 2026.
        outage = (branch_idx == 3) & (self.days[day_idx] == pd.Timestamp("2026-08-20"))
        expected = np.where(outage, expected * 0.15, expected)

        invoices_per_cell = rng.poisson(expected)
        inv_branch = np.repeat(branch_idx, invoices_per_cell)
        inv_day = np.repeat(day_idx, invoices_per_cell)
        seq = pd.Series(np.ones(len(inv_day), dtype=int)).groupby([inv_branch, inv_day]).cumsum()

        codes = np.array([b[1] for b in BRANCHES])
        day_str = self.days.strftime("%Y%m%d").to_numpy()
        invoice_no = (
            pd.Series(codes[inv_branch])
            + "-"
            + day_str[inv_day]
            + "-"
            + seq.map("{:04d}".format).to_numpy()
        )

        lines_per_invoice = 1 + rng.poisson(1.8, size=len(invoice_no))
        line_invoice = np.repeat(np.arange(len(invoice_no)), lines_per_invoice)
        sku_idx = rng.choice(
            len(products), size=len(line_invoice), p=products["popularity"].to_numpy()
        )

        category = products["category"].to_numpy()[sku_idx]
        qty = 1 + rng.poisson(np.where(category == "Bakery", 1.4, 0.7))
        discount = np.where(
            rng.random(len(sku_idx)) < 0.3, rng.choice([5, 10, 15, 20], len(sku_idx)), 0
        )

        lines = pd.DataFrame(
            {
                "invoice_no": invoice_no.to_numpy()[line_invoice],
                "invoice_date": self.days[inv_day[line_invoice]].strftime("%Y-%m-%d"),
                "branch": np.array([b[0] for b in BRANCHES])[inv_branch[line_invoice]],
                "sku": products["sku"].to_numpy()[sku_idx],
                "quantity": qty,
                "discount_pct": discount,
            }
        )
        # One line per invoice+SKU (the business key): merge repeated picks.
        lines = lines.groupby(
            ["invoice_no", "invoice_date", "branch", "sku"], as_index=False, sort=False
        ).agg(quantity=("quantity", "sum"), discount_pct=("discount_pct", "max"))
        lines = lines.merge(
            products[["sku", "product_name", "category", "unit_price", "cost_price"]], on="sku"
        )

        # Potential anomalies: a few bulk purchases (quantity spikes).
        spikes = rng.choice(len(lines), size=max(3, int(8 * self.scale)), replace=False)
        lines.loc[spikes, "quantity"] = rng.integers(40, 90, size=len(spikes))
        self.injected["anomaly_bulk_purchases"] = len(spikes)
        self.injected["anomaly_outage_days"] = 1

        columns = [
            "invoice_no",
            "invoice_date",
            "branch",
            "category",
            "sku",
            "product_name",
            "quantity",
            "unit_price",
            "discount_pct",
            "cost_price",
        ]
        return lines.sort_values(["invoice_date", "invoice_no", "sku"])[columns].reset_index(
            drop=True
        )

    def _inject_sales_issues(self, clean: pd.DataFrame) -> pd.DataFrame:
        rng = self.rng
        df = clean.astype(object).copy()
        n = len(df)

        def pick(count: int) -> np.ndarray:
            return rng.choice(n, size=min(count, n), replace=False)

        missing_cat = pick(int(n * 0.003))
        df.loc[missing_cat, "category"] = None
        missing_branch = pick(int(n * 0.002))
        df.loc[missing_branch, "branch"] = None
        negative = pick(12)
        df.loc[negative, "quantity"] = -df.loc[negative, "quantity"].astype(int)
        bad_dates = pick(15)
        df.loc[bad_dates, "invoice_date"] = rng.choice(
            ["2026-02-30", "31/13/2026", "TBD"], len(bad_dates)
        )
        bad_numbers = pick(20)
        df.loc[bad_numbers, "unit_price"] = rng.choice(["N/A", "12..5", "price?"], len(bad_numbers))
        bad_discount = pick(5)
        df.loc[bad_discount, "discount_pct"] = 120
        messy = pick(int(n * 0.01))
        df.loc[messy, "branch"] = df.loc[messy, "branch"].map(
            lambda b: (
                None if b is None else (f"  {b.lower()} " if rng.random() < 0.5 else b.upper())
            )
        )

        # Exact duplicate rows placed right after their originals (double scans).
        dup_source = np.sort(pick(int(n * 0.004)))
        duplicates = df.iloc[dup_source]
        position = np.concatenate([np.arange(n, dtype=float), dup_source + 0.5])
        df = pd.concat([df, duplicates], ignore_index=True)
        df = df.iloc[np.argsort(position, kind="stable")].reset_index(drop=True)

        self.injected.update(
            {
                "sales_missing_category": len(missing_cat),
                "sales_missing_branch": len(missing_branch),
                "sales_negative_quantity": len(negative),
                "sales_invalid_dates": len(bad_dates),
                "sales_invalid_unit_price": len(bad_numbers),
                "sales_discount_over_100": len(bad_discount),
                "sales_inconsistent_branch_text": len(messy),
                "sales_duplicate_rows": len(duplicates),
            }
        )
        return df

    # --------------------------------------------------------------- inventory
    def _inventory(self, products: pd.DataFrame, sales: pd.DataFrame) -> pd.DataFrame:
        rng = self.rng
        sales_dates = pd.to_datetime(sales["invoice_date"])
        snapshots = pd.date_range("2026-07-05", END_DATE, freq="W-SUN")
        branch_names = [b[0] for b in BRANCHES]
        grid = pd.MultiIndex.from_product([branch_names, products["sku"]], names=["branch", "sku"])
        frames = []
        for snap in snapshots:  # one iteration per weekly snapshot, not per row
            window = sales[(sales_dates > snap - pd.Timedelta(days=28)) & (sales_dates <= snap)]
            rate = (
                window.groupby(["branch", "sku"])["quantity"].sum().reindex(grid, fill_value=0) / 28
            )
            upto = sales[sales_dates <= snap]
            last_sale = upto.groupby(["branch", "sku"])["invoice_date"].max().reindex(grid)
            cover_days = rng.lognormal(mean=np.log(22), sigma=0.55, size=len(grid))
            stock = np.ceil(rate.to_numpy() * cover_days + rng.integers(0, 6, len(grid)))
            stockout = rng.random(len(grid)) < 0.03
            stock[stockout] = 0
            frame = pd.DataFrame(
                {
                    "snapshot_date": snap.strftime("%Y-%m-%d"),
                    "branch": grid.get_level_values("branch"),
                    "sku": grid.get_level_values("sku"),
                    "stock_qty": stock.astype(int),
                    "avg_daily_sales": rate.round(2).to_numpy(),
                    "last_sale_date": last_sale.to_numpy(),
                }
            )
            frames.append(frame)
        inventory = pd.concat(frames, ignore_index=True)
        inventory = inventory.merge(
            products[["sku", "product_name", "category", "cost_price"]], on="sku"
        )
        inventory = inventory.rename(columns={"cost_price": "unit_cost"})
        columns = [
            "snapshot_date",
            "branch",
            "sku",
            "product_name",
            "category",
            "stock_qty",
            "unit_cost",
            "avg_daily_sales",
            "last_sale_date",
        ]
        return (
            inventory[columns]
            .sort_values(["snapshot_date", "branch", "sku"])
            .reset_index(drop=True)
        )

    # ------------------------------------------------------------------ orders
    def _orders(self, employees: pd.DataFrame, count: int = 3200) -> pd.DataFrame:
        rng = self.rng
        n = max(50, int(count * self.scale))
        branch_names = [b[0] for b in BRANCHES]
        department = rng.choice(DEPARTMENTS, size=n, p=(0.35, 0.15, 0.18, 0.15, 0.1, 0.07))
        request_type = [rng.choice(REQUEST_TYPES[d]) for d in department]
        priority = rng.choice(PRIORITIES, size=n, p=PRIORITY_WEIGHTS)

        day = rng.choice(self.days, size=n)
        slot = rng.random(n)
        hour = np.where(
            slot < 0.80,
            rng.uniform(8, 17, n),
            np.where(slot < 0.92, rng.uniform(17, 22, n), rng.uniform(6, 8, n)),
        )
        created = pd.to_datetime(day) + pd.to_timedelta(np.round(hour * 60), unit="min")

        status = rng.choice(
            ("completed", "in_progress", "open", "cancelled"), size=n, p=(0.82, 0.08, 0.06, 0.04)
        )
        sla = np.array([SLA_HOURS[p] for p in priority], dtype=float)
        # ~78% of completed requests land within SLA, the rest overrun.
        working_hours = sla * rng.lognormal(mean=np.log(0.65), sigma=0.55, size=n)
        holidays = {date.fromisoformat(d) for d, _ in HOLIDAYS}
        completed = [
            _add_working_hours(c.to_pydatetime(), h, holidays) if s == "completed" else None
            for c, h, s in zip(created, working_hours, status, strict=True)
        ]

        value = np.where(
            np.isin(request_type, list(VALUE_REQUEST_TYPES)),
            np.round(rng.lognormal(mean=np.log(18_000), sigma=0.9, size=n), -1),
            np.nan,
        )
        high_value = rng.choice(np.flatnonzero(~np.isnan(value)), size=12, replace=False)
        value[high_value] = rng.integers(105_000, 450_000, size=12)

        orders = pd.DataFrame(
            {
                "request_id": [f"REQ-{2026}{i + 1:05d}" for i in range(n)],
                "branch": rng.choice(branch_names, size=n),
                "department": department,
                "request_type": request_type,
                "priority": priority,
                "created_at": created.strftime("%Y-%m-%d %H:%M"),
                "completed_at": [c.strftime("%Y-%m-%d %H:%M") if c else None for c in completed],
                "status": status,
                "order_value": value,
                "assigned_to": rng.choice(employees["employee_id"], size=n),
            }
        )
        return orders.sort_values("created_at").reset_index(drop=True)

    def _inject_order_issues(self, orders: pd.DataFrame) -> pd.DataFrame:
        df = orders.astype(object).copy()
        completed = np.flatnonzero(df["status"].to_numpy() == "completed")
        reversed_rows = self.rng.choice(completed, size=6, replace=False)
        # Completion recorded before creation — a classic data-entry error.
        df.loc[reversed_rows, "completed_at"] = (
            pd.to_datetime(df.loc[reversed_rows, "created_at"]) - pd.Timedelta(hours=3)
        ).dt.strftime("%Y-%m-%d %H:%M")
        bad_priority = self.rng.choice(len(df), size=8, replace=False)
        df.loc[bad_priority, "priority"] = "urgent"
        self.injected["orders_completed_before_created"] = len(reversed_rows)
        self.injected["orders_invalid_priority"] = len(bad_priority)
        return df


def _add_working_hours(start: datetime, hours: float, holidays: set[date]) -> datetime:
    """Advance ``start`` by ``hours`` of working time (Mon-Sat, 08:00-17:00).

    Used only to synthesise realistic completion timestamps for sample data.
    """
    day_start, day_end = time(8, 0), time(17, 0)

    def is_working(d: date) -> bool:
        return d.weekday() != 6 and d not in holidays

    current = start
    if not is_working(current.date()) or current.time() >= day_end:
        current = datetime.combine(current.date() + timedelta(days=1), day_start)
    elif current.time() < day_start:
        current = datetime.combine(current.date(), day_start)
    while not is_working(current.date()):
        current = datetime.combine(current.date() + timedelta(days=1), day_start)

    remaining = timedelta(hours=hours)
    while True:
        end_of_day = datetime.combine(current.date(), day_end)
        available = end_of_day - current
        if remaining <= available:
            return current + remaining
        remaining -= available
        current = datetime.combine(current.date() + timedelta(days=1), day_start)
        while not is_working(current.date()):
            current += timedelta(days=1)
