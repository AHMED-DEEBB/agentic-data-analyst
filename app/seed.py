"""Synthetic GCC retail dataset (deterministic, no real company data).

A fixed random seed and fixed date range (2024-2025) mean every install produces the
exact same data, so evaluation results are reproducible on any machine or in CI.
"""

import random
from datetime import date, timedelta

DDL = """
CREATE TABLE IF NOT EXISTS stores (
    store_id    INT PRIMARY KEY,
    name        TEXT NOT NULL,
    city        TEXT NOT NULL,
    country     TEXT NOT NULL,
    opened_on   DATE NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
    customer_id INT PRIMARY KEY,
    full_name   TEXT NOT NULL,
    city        TEXT NOT NULL,
    country     TEXT NOT NULL,
    segment     TEXT NOT NULL CHECK (segment IN ('Retail', 'Business')),
    signup_date DATE NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    product_id  INT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    unit_price  NUMERIC(10, 2) NOT NULL,
    unit_cost   NUMERIC(10, 2) NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    order_id    INT PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers(customer_id),
    store_id    INT NOT NULL REFERENCES stores(store_id),
    order_date  DATE NOT NULL,
    channel     TEXT NOT NULL CHECK (channel IN ('Online', 'In-Store')),
    status      TEXT NOT NULL CHECK (status IN ('Completed', 'Cancelled', 'Returned'))
);
CREATE TABLE IF NOT EXISTS order_items (
    order_item_id INT PRIMARY KEY,
    order_id      INT NOT NULL REFERENCES orders(order_id),
    product_id    INT NOT NULL REFERENCES products(product_id),
    quantity      INT NOT NULL,
    unit_price    NUMERIC(10, 2) NOT NULL,
    discount      NUMERIC(4, 2) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id);
"""

STORES = [
    (1, "Dubai Mall", "Dubai", "UAE", date(2019, 3, 1)),
    (2, "Mall of the Emirates", "Dubai", "UAE", date(2020, 6, 15)),
    (3, "Yas Mall", "Abu Dhabi", "UAE", date(2020, 11, 1)),
    (4, "Sahara Centre", "Sharjah", "UAE", date(2021, 4, 10)),
    (5, "Riyadh Park", "Riyadh", "Saudi Arabia", date(2021, 9, 1)),
    (6, "Red Sea Mall", "Jeddah", "Saudi Arabia", date(2022, 2, 20)),
    (7, "Doha Festival City", "Doha", "Qatar", date(2022, 8, 5)),
    (8, "The Avenues", "Kuwait City", "Kuwait", date(2023, 1, 15)),
]

CATEGORIES = {
    "Electronics": ["Smartphone", "Laptop", "Wireless Earbuds", "Smartwatch", "Tablet", "Bluetooth Speaker", "Gaming Console"],
    "Home & Kitchen": ["Air Fryer", "Coffee Machine", "Blender", "Cookware Set", "Vacuum Cleaner", "Water Purifier", "Microwave"],
    "Furniture": ["Office Chair", "Sofa", "Dining Table", "Bookshelf", "Bed Frame", "Standing Desk"],
    "Fashion": ["Abaya", "Kandura", "Sneakers", "Leather Bag", "Sunglasses", "Watch", "Jacket"],
    "Beauty": ["Oud Perfume", "Skincare Set", "Hair Dryer", "Makeup Kit", "Bakhoor Burner", "Face Serum"],
    "Sports": ["Treadmill", "Yoga Mat", "Dumbbell Set", "Bicycle", "Football", "Tennis Racket", "Fitness Tracker"],
}
PRICE_RANGE = {
    "Electronics": (150, 4500), "Home & Kitchen": (80, 1500), "Furniture": (250, 3500),
    "Fashion": (60, 1800), "Beauty": (40, 900), "Sports": (30, 3000),
}
FIRST = ["Ahmed", "Mohammed", "Omar", "Khalid", "Youssef", "Ali", "Hassan", "Fatima", "Aisha", "Mariam",
         "Noura", "Layla", "Sara", "Hind", "Rashid", "Saeed", "Huda", "Reem", "Tariq", "Salma"]
LAST = ["Al Mansouri", "Al Hashimi", "Al Qasimi", "Haddad", "Nasser", "Al Farsi", "Saleh", "Khoury",
        "Al Suwaidi", "Al Harbi", "Al Otaibi", "Ibrahim", "Mahmoud", "Al Kaabi", "Al Thani"]

N_CUSTOMERS = 600
N_ORDERS = 6000
START, END = date(2024, 1, 1), date(2025, 12, 31)


def generate(seed: int = 42) -> dict[str, list[tuple]]:
    rng = random.Random(seed)

    products, pid = [], 1
    for category, names in CATEGORIES.items():
        lo, hi = PRICE_RANGE[category]
        for name in names:
            price = round(rng.uniform(lo, hi), 2)
            cost = round(price * rng.uniform(0.45, 0.75), 2)
            products.append((pid, name, category, price, cost))
            pid += 1

    customers = []
    for cid in range(1, N_CUSTOMERS + 1):
        store = rng.choice(STORES)
        signup = START - timedelta(days=rng.randint(0, 700)) + timedelta(days=rng.randint(0, 600))
        customers.append((
            cid, f"{rng.choice(FIRST)} {rng.choice(LAST)}", store[2], store[3],
            "Business" if rng.random() < 0.2 else "Retail", signup,
        ))

    # Weighted store traffic and seasonal demand (Ramadan/Eid and Nov-Dec peaks).
    store_weights = [18, 16, 13, 8, 14, 10, 11, 10]
    month_weights = [8, 7, 11, 12, 8, 7, 7, 8, 9, 10, 13, 14]
    days = [START + timedelta(days=i) for i in range((END - START).days + 1)]
    day_weights = [month_weights[d.month - 1] * (1.15 if d.year == 2025 else 1.0) for d in days]

    orders, items, item_id = [], [], 1
    for oid in range(1, N_ORDERS + 1):
        customer = customers[rng.randrange(N_CUSTOMERS)]
        store = rng.choices(STORES, weights=store_weights)[0]
        order_date = rng.choices(days, weights=day_weights)[0]
        channel = "Online" if rng.random() < (0.38 if order_date.year == 2024 else 0.47) else "In-Store"
        r = rng.random()
        status = "Completed" if r < 0.88 else ("Cancelled" if r < 0.95 else "Returned")
        orders.append((oid, customer[0], store[0], order_date, channel, status))

        for product in rng.sample(products, rng.choices([1, 2, 3, 4], weights=[45, 30, 17, 8])[0]):
            qty = rng.choices([1, 2, 3, 5], weights=[70, 20, 7, 3])[0]
            discount = rng.choices([0, 0.05, 0.10, 0.20], weights=[65, 15, 12, 8])[0]
            items.append((item_id, oid, product[0], qty, product[3], discount))
            item_id += 1

    return {
        "stores": STORES, "customers": customers, "products": products,
        "orders": orders, "order_items": items,
    }


def seed(conn) -> bool:
    """Create tables and load data if empty. Returns True if data was inserted."""
    conn.execute(DDL)
    if conn.execute("SELECT count(*) FROM orders").fetchone()[0] > 0:
        return False
    data = generate()
    for table in ["stores", "customers", "products", "orders", "order_items"]:
        with conn.cursor().copy(f"COPY {table} FROM STDIN") as copy:
            for row in data[table]:
                copy.write_row(row)
    conn.commit()
    return True
