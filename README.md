# SmartPOS

A desktop Point of Sale and Inventory Management System.
Python + PyQt5 + MySQL. No web framework, no ORM — plain SQL through
`mysql-connector-python`.

> **Status: scaffold.** The structure, the database schema and the module
> contracts are in place. The function bodies are not — every stub raises
> `NotImplementedError`.

## Requirements

- Python 3.11+
- Docker Desktop (runs MySQL; the app itself runs natively)

## Getting started

```bash
cp .env.example .env

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt

docker compose up -d              # MySQL on :3306, phpMyAdmin on :8080
```

The schema and seed data load automatically the first time the container
starts, from `docker/mysql/init/`.

Then run the app:

```bash
PYTHONPATH=src python -m smartpos
```

Or use the helper, which starts the database, waits for it and launches the app:

```bash
./scripts/run_dev.sh
```

Default login: **admin / admin123** — change it after the first sign-in.

## Why Docker only covers the database

A PyQt5 application needs a display server, which a container does not have. The
database, though, benefits from being containerized: every team member gets the
same MySQL 8 with the same schema and no local install. So `docker compose`
runs MySQL and phpMyAdmin, and the app runs on your machine against it.

If the shop already has its own MySQL server, skip Docker entirely and apply the
schema with `python scripts/init_db.py`.

## Layout

```
smartpos/
├── docker/mysql/
│   ├── init/                  schema + seed, applied on first container start
│   │   ├── 001_schema.sql     users, warehouses, categories, products, parties
│   │   ├── 002_transactions.sql  purchase, sale, sale return, stock, payments
│   │   └── 003_seed.sql       default warehouse, units, admin user, settings
│   └── my.cnf                 InnoDB, utf8mb4, READ-COMMITTED, strict mode
├── docs/ARCHITECTURE.md       layer rules and the data-consistency invariants
├── scripts/
│   ├── init_db.py             apply the schema to a non-Docker MySQL
│   └── run_dev.sh             start db, wait, launch app
├── src/smartpos/
│   ├── main.py                entry point  (python -m smartpos)
│   ├── config.py              settings from .env
│   ├── core/                  constants, exceptions, logging, password hashing
│   ├── db/                    connection pool, transactions, repository base
│   ├── models/                dataclasses, one per table
│   ├── services/              business rules — the layer worth testing
│   ├── ui/                    PyQt5: main window, login, 15 views, stylesheet
│   ├── reports/               invoice rendering, PDF/Excel export
│   └── utils/                 barcode, formatting, validators
└── tests/
```

## Database

15 tables. The ones that carry the most weight:

| Table | Holds |
|---|---|
| `stock` | quantity on hand, one row per product per warehouse |
| `stock_movements` | append-only ledger; every stock change with its source document |
| `sales` / `sale_items` | invoices; line items keep `unit_cost` for profit reporting |
| `sale_returns` / `sale_return_items` | returns, linked back to the original sale line |
| `purchases` / `purchase_items` | supplier purchases, with payment status |
| `stock_transfers` | warehouse-to-warehouse movement |

`stock` is the fast lookup; `stock_movements` is the audit trail. They are
always written together, in one transaction, through
`StockService.apply_movement()`.

## Build order

The dependency order that avoids rework:

1. `config` → `db/connection` → `core/logging` (app starts, connects)
2. `core/security` + `services/auth_service` + `ui/login_dialog` (sign in)
3. `ui/main_window` with an empty stack (a window appears)
4. Master data: warehouse → category → product → customer → supplier
5. `services/stock_service` — build this before anything that moves stock
6. `purchase_service` (stock in) → `sale_service` (stock out) → `sale_return_service`
7. `report_service`, `reports/`, `backup_service`

## Development

```bash
pytest                  # tests/test_structure.py checks every module imports
ruff check src tests
black src tests
```
