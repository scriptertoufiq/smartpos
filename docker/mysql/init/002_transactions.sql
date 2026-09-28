-- ---------------------------------------------------------------------------
-- SmartPOS - transactional tables (purchase, sale, sale return, stock)
-- ---------------------------------------------------------------------------

SET NAMES utf8mb4;

-- -------------------------------- purchase --------------------------------

CREATE TABLE purchases (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    reference_no   VARCHAR(30)     NOT NULL,
    supplier_id    INT UNSIGNED    NOT NULL,
    warehouse_id   INT UNSIGNED    NOT NULL,
    user_id        INT UNSIGNED    NULL,
    purchase_date  DATE            NOT NULL,
    subtotal       DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    discount       DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    tax_amount     DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    grand_total    DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    paid_amount    DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    payment_status ENUM('paid', 'partial', 'due') NOT NULL DEFAULT 'due',
    note           TEXT            NULL,
    created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_purchases_reference (reference_no),
    KEY idx_purchases_supplier (supplier_id),
    KEY idx_purchases_date (purchase_date),
    CONSTRAINT fk_purchases_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers (id),
    CONSTRAINT fk_purchases_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouses (id),
    CONSTRAINT fk_purchases_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE purchase_items (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    purchase_id BIGINT UNSIGNED NOT NULL,
    product_id  INT UNSIGNED    NOT NULL,
    quantity    DECIMAL(12, 3)  NOT NULL,
    unit_cost   DECIMAL(12, 2)  NOT NULL,
    discount    DECIMAL(12, 2)  NOT NULL DEFAULT 0.00,
    tax_amount  DECIMAL(12, 2)  NOT NULL DEFAULT 0.00,
    line_total  DECIMAL(14, 2)  NOT NULL,
    PRIMARY KEY (id),
    KEY idx_purchase_items_purchase (purchase_id),
    KEY idx_purchase_items_product (product_id),
    CONSTRAINT fk_purchase_items_purchase FOREIGN KEY (purchase_id)
        REFERENCES purchases (id) ON DELETE CASCADE,
    CONSTRAINT fk_purchase_items_product FOREIGN KEY (product_id) REFERENCES products (id)
) ENGINE=InnoDB;

-- ---------------------------------- sale ----------------------------------

CREATE TABLE sales (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    invoice_no     VARCHAR(30)     NOT NULL,
    customer_id    INT UNSIGNED    NOT NULL,
    warehouse_id   INT UNSIGNED    NOT NULL,
    user_id        INT UNSIGNED    NULL,
    sale_date      DATETIME        NOT NULL,
    subtotal       DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    discount       DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    tax_amount     DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    grand_total    DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    paid_amount    DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    change_amount  DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    payment_method ENUM('cash', 'card', 'mobile', 'mixed') NOT NULL DEFAULT 'cash',
    payment_status ENUM('paid', 'partial', 'due') NOT NULL DEFAULT 'paid',
    status         ENUM('completed', 'held', 'cancelled') NOT NULL DEFAULT 'completed',
    note           TEXT            NULL,
    created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sales_invoice (invoice_no),
    KEY idx_sales_customer (customer_id),
    KEY idx_sales_date (sale_date),
    KEY idx_sales_status (status),
    CONSTRAINT fk_sales_customer FOREIGN KEY (customer_id) REFERENCES customers (id),
    CONSTRAINT fk_sales_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouses (id),
    CONSTRAINT fk_sales_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE sale_items (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    sale_id    BIGINT UNSIGNED NOT NULL,
    product_id INT UNSIGNED    NOT NULL,
    quantity   DECIMAL(12, 3)  NOT NULL,
    unit_price DECIMAL(12, 2)  NOT NULL,
    -- Cost at the moment of sale, so profit reports stay correct when the
    -- product's purchase price changes later.
    unit_cost  DECIMAL(12, 2)  NOT NULL DEFAULT 0.00,
    discount   DECIMAL(12, 2)  NOT NULL DEFAULT 0.00,
    tax_amount DECIMAL(12, 2)  NOT NULL DEFAULT 0.00,
    line_total DECIMAL(14, 2)  NOT NULL,
    PRIMARY KEY (id),
    KEY idx_sale_items_sale (sale_id),
    KEY idx_sale_items_product (product_id),
    CONSTRAINT fk_sale_items_sale FOREIGN KEY (sale_id) REFERENCES sales (id) ON DELETE CASCADE,
    CONSTRAINT fk_sale_items_product FOREIGN KEY (product_id) REFERENCES products (id)
) ENGINE=InnoDB;

-- ------------------------------- sale return ------------------------------

CREATE TABLE sale_returns (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    reference_no  VARCHAR(30)     NOT NULL,
    sale_id       BIGINT UNSIGNED NOT NULL,
    warehouse_id  INT UNSIGNED    NOT NULL,
    user_id       INT UNSIGNED    NULL,
    return_date   DATETIME        NOT NULL,
    total_amount  DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    refund_method ENUM('cash', 'due_adjustment') NOT NULL DEFAULT 'cash',
    reason        VARCHAR(255)    NULL,
    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sale_returns_reference (reference_no),
    KEY idx_sale_returns_sale (sale_id),
    KEY idx_sale_returns_date (return_date),
    CONSTRAINT fk_sale_returns_sale FOREIGN KEY (sale_id) REFERENCES sales (id),
    CONSTRAINT fk_sale_returns_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouses (id),
    CONSTRAINT fk_sale_returns_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE sale_return_items (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    sale_return_id BIGINT UNSIGNED NOT NULL,
    sale_item_id   BIGINT UNSIGNED NOT NULL,
    product_id     INT UNSIGNED    NOT NULL,
    quantity       DECIMAL(12, 3)  NOT NULL,
    unit_price     DECIMAL(12, 2)  NOT NULL,
    line_total     DECIMAL(14, 2)  NOT NULL,
    PRIMARY KEY (id),
    KEY idx_sale_return_items_return (sale_return_id),
    CONSTRAINT fk_sale_return_items_return FOREIGN KEY (sale_return_id)
        REFERENCES sale_returns (id) ON DELETE CASCADE,
    CONSTRAINT fk_sale_return_items_sale_item FOREIGN KEY (sale_item_id)
        REFERENCES sale_items (id),
    CONSTRAINT fk_sale_return_items_product FOREIGN KEY (product_id) REFERENCES products (id)
) ENGINE=InnoDB;

-- ---------------------------------- stock ---------------------------------

-- Current quantity on hand, one row per product per warehouse.
CREATE TABLE stock (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id   INT UNSIGNED    NOT NULL,
    warehouse_id INT UNSIGNED    NOT NULL,
    quantity     DECIMAL(12, 3)  NOT NULL DEFAULT 0.000,
    updated_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_stock_product_warehouse (product_id, warehouse_id),
    CONSTRAINT fk_stock_product FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE,
    CONSTRAINT fk_stock_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouses (id)
) ENGINE=InnoDB;

-- Append-only ledger: every change to `stock` is also written here, so the
-- quantity on hand can always be explained by its source document.
CREATE TABLE stock_movements (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id     INT UNSIGNED    NOT NULL,
    warehouse_id   INT UNSIGNED    NOT NULL,
    movement_type  ENUM('purchase', 'sale', 'sale_return', 'transfer_in',
                        'transfer_out', 'adjustment', 'opening') NOT NULL,
    quantity       DECIMAL(12, 3)  NOT NULL COMMENT 'signed: positive in, negative out',
    balance_after  DECIMAL(12, 3)  NOT NULL,
    reference_type VARCHAR(30)     NULL,
    reference_id   BIGINT UNSIGNED NULL,
    user_id        INT UNSIGNED    NULL,
    note           VARCHAR(255)    NULL,
    created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_movements_product_warehouse (product_id, warehouse_id),
    KEY idx_movements_created (created_at),
    KEY idx_movements_reference (reference_type, reference_id),
    CONSTRAINT fk_movements_product FOREIGN KEY (product_id) REFERENCES products (id),
    CONSTRAINT fk_movements_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouses (id),
    CONSTRAINT fk_movements_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
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
    CONSTRAINT fk_transfers_from FOREIGN KEY (from_warehouse_id) REFERENCES warehouses (id),
    CONSTRAINT fk_transfers_to FOREIGN KEY (to_warehouse_id) REFERENCES warehouses (id),
    CONSTRAINT fk_transfers_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE stock_transfer_items (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    transfer_id BIGINT UNSIGNED NOT NULL,
    product_id  INT UNSIGNED    NOT NULL,
    quantity    DECIMAL(12, 3)  NOT NULL,
    PRIMARY KEY (id),
    KEY idx_transfer_items_transfer (transfer_id),
    CONSTRAINT fk_transfer_items_transfer FOREIGN KEY (transfer_id)
        REFERENCES stock_transfers (id) ON DELETE CASCADE,
    CONSTRAINT fk_transfer_items_product FOREIGN KEY (product_id) REFERENCES products (id)
) ENGINE=InnoDB;

-- ------------------------------- payments ---------------------------------

CREATE TABLE payments (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    party_type   ENUM('customer', 'supplier') NOT NULL,
    party_id     INT UNSIGNED    NOT NULL,
    direction    ENUM('in', 'out') NOT NULL,
    amount       DECIMAL(14, 2)  NOT NULL,
    method       ENUM('cash', 'card', 'mobile', 'bank') NOT NULL DEFAULT 'cash',
    reference_type VARCHAR(30)   NULL,
    reference_id BIGINT UNSIGNED NULL,
    user_id      INT UNSIGNED    NULL,
    paid_at      DATETIME        NOT NULL,
    note         VARCHAR(255)    NULL,
    created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_payments_party (party_type, party_id),
    KEY idx_payments_paid_at (paid_at),
    CONSTRAINT fk_payments_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- ------------------------------- settings ---------------------------------

CREATE TABLE settings (
    setting_key   VARCHAR(60)  NOT NULL,
    setting_value TEXT         NULL,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (setting_key)
) ENGINE=InnoDB;
