-- ---------------------------------------------------------------------------
-- SmartPOS · 004 · Money, numbering and settings
--
-- Customer and supplier payments are separate tables so that each carries a
-- real foreign key to its party (design decision 8.2). The cost is that a
-- combined cash position needs a UNION; see the view at the end of this file.
-- ---------------------------------------------------------------------------

SET NAMES utf8mb4;

-- Money in from a customer: counter tender, later due collection, or -- with
-- direction 'out' -- a cash refund on a return.
CREATE TABLE customer_payments (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    customer_id    INT UNSIGNED    NOT NULL,
    direction      ENUM('in', 'out') NOT NULL DEFAULT 'in'
                   COMMENT 'out = cash refunded to the customer',
    amount         DECIMAL(14, 2)  NOT NULL COMMENT 'always positive; direction carries the sign',
    method         ENUM('cash', 'card', 'mobile', 'bank') NOT NULL DEFAULT 'cash',
    -- A 'mixed' sale is several rows against the same reference_id:
    -- 500 cash + 300 card. That is why sales.payment_method can be 'mixed'
    -- without any extra table.
    reference_type VARCHAR(30)     NULL COMMENT 'sale | sale_return | NULL for a plain collection',
    reference_id   BIGINT UNSIGNED NULL,
    user_id        INT UNSIGNED    NULL,
    paid_at        DATETIME        NOT NULL,
    note           VARCHAR(255)    NULL,
    created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_cust_pay_customer (customer_id),
    KEY idx_cust_pay_paid_at (paid_at),
    KEY idx_cust_pay_reference (reference_type, reference_id),
    CONSTRAINT fk_cust_pay_customer FOREIGN KEY (customer_id) REFERENCES customers (id),
    CONSTRAINT fk_cust_pay_user     FOREIGN KEY (user_id)     REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Money out to a supplier, or back in from one on a credit note.
CREATE TABLE supplier_payments (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    supplier_id    INT UNSIGNED    NOT NULL,
    direction      ENUM('in', 'out') NOT NULL DEFAULT 'out'
                   COMMENT 'in = money received back from the supplier',
    amount         DECIMAL(14, 2)  NOT NULL,
    method         ENUM('cash', 'card', 'mobile', 'bank') NOT NULL DEFAULT 'cash',
    reference_type VARCHAR(30)     NULL COMMENT 'purchase | NULL for a plain payment',
    reference_id   BIGINT UNSIGNED NULL,
    user_id        INT UNSIGNED    NULL,
    paid_at        DATETIME        NOT NULL,
    note           VARCHAR(255)    NULL,
    created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_supp_pay_supplier (supplier_id),
    KEY idx_supp_pay_paid_at (paid_at),
    KEY idx_supp_pay_reference (reference_type, reference_id),
    CONSTRAINT fk_supp_pay_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers (id),
    CONSTRAINT fk_supp_pay_user     FOREIGN KEY (user_id)     REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Gapless, concurrency-safe document numbers. Allocated inside the document's
-- own transaction:
--
--   SELECT next_number FROM document_sequences
--    WHERE doc_type = ? AND period = ? FOR UPDATE;
--   UPDATE document_sequences SET next_number = next_number + 1
--    WHERE doc_type = ? AND period = ?;
--
-- Sharing the document's transaction is what makes it gapless: a rolled-back
-- sale gives its number back. MAX(invoice_no) + 1 does neither.
CREATE TABLE document_sequences (
    doc_type    VARCHAR(20)     NOT NULL COMMENT 'sale | purchase | sale_return | transfer',
    period      VARCHAR(7)      NOT NULL DEFAULT '' COMMENT 'reset period, e.g. 2026',
    prefix      VARCHAR(10)     NOT NULL DEFAULT '',
    next_number BIGINT UNSIGNED NOT NULL DEFAULT 1,
    padding     TINYINT         NOT NULL DEFAULT 6 COMMENT 'INV-2026-000042',
    updated_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (doc_type, period)
) ENGINE=InnoDB;

CREATE TABLE settings (
    setting_key   VARCHAR(60) NOT NULL,
    setting_value TEXT        NULL,
    updated_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (setting_key)
) ENGINE=InnoDB;

-- The UNION that decision 8.2 costs us, written once here so no report has to
-- repeat it. "What cash moved today" is a single SELECT against this view.
CREATE OR REPLACE VIEW v_cash_movements AS
    SELECT 'customer' AS party_type, customer_id AS party_id,
           direction, amount, method, reference_type, reference_id,
           user_id, paid_at
      FROM customer_payments
    UNION ALL
    SELECT 'supplier' AS party_type, supplier_id AS party_id,
           direction, amount, method, reference_type, reference_id,
           user_id, paid_at
      FROM supplier_payments;
