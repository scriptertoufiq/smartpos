-- ---------------------------------------------------------------------------
-- SmartPOS · 003 · Stock
--
-- `stock` is a cache of the quantity on hand. `stock_movements` is the truth:
-- an append-only ledger in which every change names the document that caused
-- it. The two are always written together, in one transaction, through
-- StockService.apply_movement().
--
-- The invariant, which must hold at all times:
--
--   SELECT product_id, warehouse_id
--   FROM   stock_movements
--   GROUP  BY product_id, warehouse_id
--   HAVING SUM(quantity) <> (SELECT quantity FROM stock s
--                            WHERE s.product_id   = stock_movements.product_id
--                              AND s.warehouse_id = stock_movements.warehouse_id);
--
--   -> must return zero rows.
-- ---------------------------------------------------------------------------

SET NAMES utf8mb4;

-- The lock target. A sale runs SELECT ... FOR UPDATE on this row, so two
-- counters cannot both sell the last unit. Rows are created lazily with
-- INSERT ... ON DUPLICATE KEY UPDATE.
CREATE TABLE stock (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id   INT UNSIGNED    NOT NULL,
    warehouse_id INT UNSIGNED    NOT NULL,
    quantity     DECIMAL(12, 3)  NOT NULL DEFAULT 0.000,
    updated_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_stock_product_warehouse (product_id, warehouse_id),
    KEY idx_stock_warehouse (warehouse_id),
    CONSTRAINT fk_stock_product FOREIGN KEY (product_id)
        REFERENCES products (id) ON DELETE CASCADE,
    CONSTRAINT fk_stock_warehouse FOREIGN KEY (warehouse_id)
        REFERENCES warehouses (id)
) ENGINE=InnoDB;

CREATE TABLE stock_movements (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id     INT UNSIGNED    NOT NULL,
    warehouse_id   INT UNSIGNED    NOT NULL,
    -- 'sale_cancel' reverses a cancelled sale; it is kept distinct from
    -- 'sale_return' so the ledger can tell a cancellation from a real return.
    movement_type  ENUM('opening', 'purchase', 'sale', 'sale_return', 'sale_cancel',
                        'transfer_in', 'transfer_out', 'adjustment') NOT NULL,
    quantity       DECIMAL(12, 3)  NOT NULL COMMENT 'signed: positive in, negative out',
    -- Running balance, written while holding the stock row lock above.
    balance_after  DECIMAL(12, 3)  NOT NULL,
    reference_type VARCHAR(30)     NULL,
    reference_id   BIGINT UNSIGNED NULL,
    user_id        INT UNSIGNED    NULL,
    note           VARCHAR(255)    NULL COMMENT 'required when movement_type = adjustment',
    created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_movements_product_warehouse (product_id, warehouse_id),
    -- The ledger screen orders by time within a product and warehouse.
    KEY idx_movements_ledger (product_id, warehouse_id, created_at),
    KEY idx_movements_created (created_at),
    KEY idx_movements_reference (reference_type, reference_id),
    KEY idx_movements_warehouse (warehouse_id),
    CONSTRAINT fk_movements_product   FOREIGN KEY (product_id)   REFERENCES products (id),
    CONSTRAINT fk_movements_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouses (id),
    CONSTRAINT fk_movements_user      FOREIGN KEY (user_id)      REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE stock_transfers (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    reference_no      VARCHAR(30)     NOT NULL,
    from_warehouse_id INT UNSIGNED    NOT NULL,
    to_warehouse_id   INT UNSIGNED    NOT NULL,
    user_id           INT UNSIGNED    NULL,
    transfer_date     DATETIME        NOT NULL,
    note              VARCHAR(255)    NULL,
    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_transfers_reference (reference_no),
    KEY idx_transfers_from (from_warehouse_id),
    KEY idx_transfers_to (to_warehouse_id),
    KEY idx_transfers_date (transfer_date),
    -- A transfer to itself would write two cancelling movements and confuse
    -- the ledger for no purpose.
    CONSTRAINT ck_transfer_distinct CHECK (from_warehouse_id <> to_warehouse_id),
    CONSTRAINT fk_transfers_from FOREIGN KEY (from_warehouse_id) REFERENCES warehouses (id),
    CONSTRAINT fk_transfers_to   FOREIGN KEY (to_warehouse_id)   REFERENCES warehouses (id),
    CONSTRAINT fk_transfers_user FOREIGN KEY (user_id)           REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE stock_transfer_items (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    transfer_id BIGINT UNSIGNED NOT NULL,
    product_id  INT UNSIGNED    NOT NULL,
    quantity    DECIMAL(12, 3)  NOT NULL,
    PRIMARY KEY (id),
    -- One line per product per transfer.
    UNIQUE KEY uq_transfer_item (transfer_id, product_id),
    KEY idx_transfer_items_product (product_id),
    CONSTRAINT fk_transfer_items_transfer FOREIGN KEY (transfer_id)
        REFERENCES stock_transfers (id) ON DELETE CASCADE,
    CONSTRAINT fk_transfer_items_product FOREIGN KEY (product_id)
        REFERENCES products (id)
) ENGINE=InnoDB;
