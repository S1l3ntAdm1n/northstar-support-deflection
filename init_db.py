import os
import sqlite3


def initialize_database():
    """
    Initializes a local SQLite database named 'database.db' in the project directory.
    Creates tables for 'orders', 'inventory', and 'tickets', and seeds them with mock data.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(base_dir, "database.db")

    print(f"Connecting to database at: {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # ==========================================================================
    # 1. CREATE SCHEMA TABLES
    # ==========================================================================

    print("Setting up database tables...")
    cursor.execute("DROP TABLE IF EXISTS orders;")
    cursor.execute("DROP TABLE IF EXISTS inventory;")
    cursor.execute("DROP TABLE IF EXISTS tickets;")

    # orders table
    # tracking_update: last meaningful tracking event (plain text, shown to user)
    cursor.execute("""
        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            product TEXT NOT NULL,
            status TEXT NOT NULL,
            ship_date TEXT,
            eta TEXT,
            tracking_update TEXT
        );
    """)

    # inventory table
    # quantity: integer stock count; used for "only N left" vs "plenty" language
    cursor.execute("""
        CREATE TABLE inventory (
            product_name TEXT PRIMARY KEY,
            sizes TEXT NOT NULL,
            colors TEXT,
            stock_status TEXT NOT NULL,
            quantity INTEGER DEFAULT 100,
            restock_date TEXT
        );
    """)

    # tickets table for support escalations
    cursor.execute("""
        CREATE TABLE tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            customer_name TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            issue_description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ==========================================================================
    # 2. SEED MOCK DATA
    # ==========================================================================

    print("Seeding database tables...")

    # Orders seed data
    # Statuses: shipped, processing, delivered, delayed, delivery_exception
    orders_seed = [
        (
            "1001",
            "Blue Sneakers",
            "shipped",
            "2026-08-08",
            "2026-08-15",
            "Package picked up by carrier. In transit to local facility.",
        ),
        (
            "1002",
            "Red Boots",
            "processing",
            None,
            None,
            "Order confirmed. Preparing for shipment.",
        ),
        (
            "1003",
            "White Run Shoes",
            "delivered",
            "2026-08-05",
            "2026-08-10",
            "Delivered to front door on Aug 10.",
        ),
        (
            "1004",
            "Green Jacket",
            "delayed",
            "2026-08-06",
            "2026-08-11",  # eta is in the past → triggers proactive delay message
            "Package held at sorting facility since Aug 9. Carrier investigating.",
        ),
        (
            "1005",
            "Black Cap",
            "delivery_exception",
            "2026-08-07",
            "2026-08-12",
            "Address not found — carrier attempted delivery and returned package to depot.",
        ),
    ]

    cursor.executemany(
        """
        INSERT INTO orders (id, product, status, ship_date, eta, tracking_update)
        VALUES (?, ?, ?, ?, ?, ?);
    """,
        orders_seed,
    )

    # Inventory seed data (product_name must be lowercase for matching)
    inventory_seed = [
        ("blue sneakers", "8, 9, 10", "Blue", "in_stock", 45, None),
        ("red boots", "7, 8, 9, 10", "Red", "out_of_stock", 0, "2026-09-01"),
        ("white run shoes", "6, 7, 8, 9, 10, 11", "White", "in_stock", 82, None),
        (
            "green jacket",
            "S, M, L, XL",
            "Olive Green",
            "in_stock",
            2,
            None,
        ),  # low stock → "only 2 left"
        ("black cap", "One Size", "Black", "in_stock", 15, None),
        ("grey hoodie", "S, M, L, XL, XXL", "Grey", "out_of_stock", 0, "2026-09-15"),
    ]

    cursor.executemany(
        """
        INSERT INTO inventory (product_name, sizes, colors, stock_status, quantity, restock_date)
        VALUES (?, ?, ?, ?, ?, ?);
    """,
        inventory_seed,
    )

    # Seed one existing ticket for demo
    cursor.execute(
        """
        INSERT INTO tickets (order_id, customer_name, customer_email, issue_description, status)
        VALUES (?, ?, ?, ?, ?);
    """,
        (
            "1002",
            "Jane Doe",
            "jane@example.com",
            "My order status has been processing for 4 days.",
            "open",
        ),
    )

    conn.commit()
    conn.close()
    print("Database successfully initialized and seeded!")


if __name__ == "__main__":
    initialize_database()
