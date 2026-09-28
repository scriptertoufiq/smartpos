# Architecture

SmartPOS is a three-layer desktop application. Each layer only talks to the one
below it.

```
  ui/         PyQt5 windows, dialogs and views
    |         calls services, renders what they return
    v
  services/   business rules: stock checks, totals, payment status
    |         no Qt imports here, so the rules are testable headlessly
    v
  db/         connection pool, transactions, hand-written SQL
    |
    v
  MySQL 8
```

`models/` holds plain dataclasses that mirror one table row each, and is used by
every layer. `core/` holds constants, exceptions, logging and password hashing.

## Rules that keep the data consistent

1. **No SQL in the UI.** A view calls a service; the service owns the query.
2. **No Qt in the services.** Keeps business rules testable without a display.
3. **Multi-table writes run in one transaction.** A sale writes the sale, its
   items, the stock levels and the stock movements together, or writes nothing.
4. **Stock changes go through `StockService.apply_movement`.** That is the only
   place `stock` and `stock_movements` are updated, so the ledger can always
   explain the quantity on hand.
5. **Stock is checked inside the transaction**, not before it, so two counters
   cannot both sell the last unit.
6. **Sale lines store `unit_cost`** as it was at the time of sale, so changing a
   product's purchase price later does not rewrite historic profit figures.

## Money and quantities

Money is `DECIMAL(12,2)` in MySQL and `decimal.Decimal` in Python -- never
float. Quantities are `DECIMAL(12,3)` so that items sold by weight work.
