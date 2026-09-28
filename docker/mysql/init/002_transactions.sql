-- ---------------------------------------------------------------------------
-- SmartPOS · 002 · Trading documents
-- Purchases, sales and sale returns, each with its line items.
--
-- Completed documents are immutable. A mistake is corrected with a return or a
-- cancellation, never with an UPDATE.
-- ---------------------------------------------------------------------------

SET NAMES utf8mb4;

-- ------------------------------- purchase ---------------------------------

CREATE TABLE purchases (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    reference_no   VARCHAR(30)     NOT NULL,
    supplier_id    INT UNSIGNED    NOT NULL,
    warehouse_id   INT UNSIGNED    NOT NULL,
    user_id        INT UNSIGNED    NULL,
    -- DATETIME, not DATE: lets a purchase and a sale on the same day be
    -- ordered against each other.
    purchase_date  DATETIME        NOT NULL,
    subtotal       DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    discount       DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    tax_amount     DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    grand_total    DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    -- Cache of SUM(supplier_payments.amount) for this purchase. Recomputed in
    -- the same transaction that inserts the payment; never edited by hand.
    paid_amount    DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    payment_status ENUM('paid', 'partial', 'due') NOT NULL DEFAULT 'due',
    note           TEXT            NULL,
    created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_purchases_reference (reference_no),
    KEY idx_purchases_supplier (supplier_id),
    KEY idx_purchases_date (purchase_date),
    KEY idx_purchases_status_date (payment_status, purchase_date),
    CONSTRAINT fk_purchases_supplier  FOREIGN KEY (supplier_id)  REFERENCES suppliers (id),
    CONSTRAINT fk_purchases_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouses (id),
    CONSTRAINT fk_purchases_user      FOREIGN KEY (user_id)      REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- No unique key on (purchase_id, product_id): the same product may legitimately
-- appear twice on one purchase at two different costs.
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
    CONSTRAINT fk_purchase_items_product FOREIGN KEY (product_id)
        REFERENCES products (id)
) ENGINE=InnoDB;

-- --------------------------------- sale -----------------------------------

CREATE TABLE sales (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    invoice_no     VARCHAR(30)     NOT NULL,
    customer_id    INT UNSIGNED    NOT NULL,
    warehouse_id   INT UNSIGNED    NOT NULL,
    user_id        INT UNSIGNED    NULL,
    sale_date      DATETIME        NOT NULL,
    subtotal       DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    -- discount_value is what the cashier typed ("10" with type 'percent');
    -- discount is what that computed to in money.
    discount_type  ENUM('fixed', 'percent') NOT NULL DEFAULT 'fixed',
    discount_value DECIMAL(12, 2)  NOT NULL DEFAULT 0.00,
    discount       DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    tax_amount     DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    grand_total    DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    -- Cache of SUM(customer_payments.amount) for this sale.
    paid_amount    DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    change_amount  DECIMAL(14, 2)  NOT NULL DEFAULT 0.00,
    payment_method ENUM('cash', 'card', 'mobile', 'mixed') NOT NULL DEFAULT 'cash',
    payment_status ENUM('paid', 'partial', 'due')          NOT NULL DEFAULT 'paid',
    -- 'held' occupies no stock. 'cancelled' reverses it (see 004 movement types).
    status         ENUM('completed', 'held', 'cancelled')  NOT NULL DEFAULT 'completed',
    note           TEXT            NULL,
    created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sales_invoice (invoice_no),
    KEY idx_sales_customer (customer_id),
    KEY idx_sales_date (sale_date),
    -- Every report filters on status and a date range together.
    KEY idx_sales_status_date (status, sale_date),
    KEY idx_sales_warehouse_date (warehouse_id, sale_date),
    CONSTRAINT fk_sales_customer  FOREIGN KEY (customer_id)  REFERENCES customers (id),
    CONSTRAINT fk_sales_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouses (id),
    CONSTRAINT fk_sales_user      FOREIGN KEY (user_id)      REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE sale_items (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    sale_id    BIGINT UNSIGNED NOT NULL,
    product_id INT UNSIGNED    NOT NULL,
    quantity   DECIMAL(12, 3)  NOT NULL,
    unit_price DECIMAL(12, 2)  NOT NULL,
    -- Copied from the product at the moment of sale. This one column is what
    -- keeps profit reports stable when a purchase price changes later.
    unit_cost  DECIMAL(12, 2)  NOT NULL DEFAULT 0.00,
    discount   DECIMAL(12, 2)  NOT NULL DEFAULT 0.00,
    tax_amount DECIMAL(12, 2)  NOT NULL DEFAULT 0.00,
    line_total DECIMAL(14, 2)  NOT NULL,
    PRIMARY KEY (id),
    KEY idx_sale_items_sale (sale_id),
    KEY idx_sale_items_product (product_id),
    CONSTRAINT fk_sale_items_sale FOREIGN KEY (sale_id)
        REFERENCES sales (id) ON DELETE CASCADE,
    CONSTRAINT fk_sale_items_product FOREIGN KEY (product_id)
        REFERENCES products (id)
) ENGINE=InnoDB;

-- ------------------------------ sale return -------------------------------

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
    CONSTRAINT fk_sale_returns_sale      FOREIGN KEY (sale_id)      REFERENCES sales (id),
    CONSTRAINT fk_sale_returns_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouses (id),
    CONSTRAINT fk_sale_returns_user      FOREIGN KEY (user_id)      REFERENCES users (id) ON DELETE SET NULL
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
    -- One row per sold line per return. Two rows for the same sale_item in one
    -- return is a data-entry error that would silently double the stock put back.
    UNIQUE KEY uq_return_item (sale_return_id, sale_item_id),
    KEY idx_sale_return_items_sale_item (sale_item_id),
    CONSTRAINT fk_sale_return_items_return FOREIGN KEY (sale_return_id)
        REFERENCES sale_returns (id) ON DELETE CASCADE,
    CONSTRAINT fk_sale_return_items_sale_item FOREIGN KEY (sale_item_id)
        REFERENCES sale_items (id),
    CONSTRAINT fk_sale_return_items_product FOREIGN KEY (product_id)
        REFERENCES products (id)
) ENGINE=InnoDB;
