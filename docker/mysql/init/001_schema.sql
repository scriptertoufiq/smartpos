-- ---------------------------------------------------------------------------
-- SmartPOS - base schema
-- Runs automatically on first start of the MySQL container.
-- ---------------------------------------------------------------------------

SET NAMES utf8mb4;

-- --------------------------- users & access -------------------------------

CREATE TABLE users (
    id            INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    username      VARCHAR(50)   NOT NULL,
    full_name     VARCHAR(120)  NOT NULL,
    password_hash VARCHAR(255)  NOT NULL,
    role          ENUM('admin', 'manager', 'cashier') NOT NULL DEFAULT 'cashier',
    warehouse_id  INT UNSIGNED  NULL,
    is_active     TINYINT(1)    NOT NULL DEFAULT 1,
    last_login_at DATETIME      NULL,
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username)
) ENGINE=InnoDB;

CREATE TABLE activity_log (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id     INT UNSIGNED    NULL,
    action      VARCHAR(60)     NOT NULL,
    entity      VARCHAR(60)     NOT NULL,
    entity_id   BIGINT UNSIGNED NULL,
    detail      TEXT            NULL,
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_activity_user (user_id),
    KEY idx_activity_created (created_at),
    CONSTRAINT fk_activity_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- ------------------------------ master data -------------------------------

CREATE TABLE warehouses (
    id         INT UNSIGNED NOT NULL AUTO_INCREMENT,
    code       VARCHAR(20)  NOT NULL,
    name       VARCHAR(120) NOT NULL,
    address    VARCHAR(255) NULL,
    phone      VARCHAR(30)  NULL,
    is_active  TINYINT(1)   NOT NULL DEFAULT 1,
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_warehouses_code (code)
) ENGINE=InnoDB;

ALTER TABLE users
    ADD CONSTRAINT fk_users_warehouse FOREIGN KEY (warehouse_id)
        REFERENCES warehouses (id) ON DELETE SET NULL;

CREATE TABLE categories (
    id         INT UNSIGNED NOT NULL AUTO_INCREMENT,
    parent_id  INT UNSIGNED NULL,
    name       VARCHAR(120) NOT NULL,
    is_active  TINYINT(1)   NOT NULL DEFAULT 1,
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_categories_parent (parent_id),
    CONSTRAINT fk_categories_parent FOREIGN KEY (parent_id)
        REFERENCES categories (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE units (
    id         INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name       VARCHAR(40)  NOT NULL,
    short_name VARCHAR(10)  NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_units_short (short_name)
) ENGINE=InnoDB;

CREATE TABLE products (
    id             INT UNSIGNED   NOT NULL AUTO_INCREMENT,
    sku            VARCHAR(40)    NOT NULL,
    barcode        VARCHAR(64)    NULL,
    name           VARCHAR(160)   NOT NULL,
    category_id    INT UNSIGNED   NULL,
    unit_id        INT UNSIGNED   NULL,
    purchase_price DECIMAL(12, 2) NOT NULL DEFAULT 0.00,
    sale_price     DECIMAL(12, 2) NOT NULL DEFAULT 0.00,
    tax_rate       DECIMAL(5, 2)  NOT NULL DEFAULT 0.00,
    reorder_level  DECIMAL(12, 3) NOT NULL DEFAULT 0.000,
    image_path     VARCHAR(255)   NULL,
    is_active      TINYINT(1)     NOT NULL DEFAULT 1,
    created_at     DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_products_sku (sku),
    UNIQUE KEY uq_products_barcode (barcode),
    KEY idx_products_category (category_id),
    KEY idx_products_name (name),
    CONSTRAINT fk_products_category FOREIGN KEY (category_id)
        REFERENCES categories (id) ON DELETE SET NULL,
    CONSTRAINT fk_products_unit FOREIGN KEY (unit_id)
        REFERENCES units (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE customers (
    id              INT UNSIGNED   NOT NULL AUTO_INCREMENT,
    name            VARCHAR(120)   NOT NULL,
    phone           VARCHAR(30)    NULL,
    email           VARCHAR(120)   NULL,
    address         VARCHAR(255)   NULL,
    opening_balance DECIMAL(12, 2) NOT NULL DEFAULT 0.00,
    is_walk_in      TINYINT(1)     NOT NULL DEFAULT 0,
    is_active       TINYINT(1)     NOT NULL DEFAULT 1,
    created_at      DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_customers_phone (phone),
    KEY idx_customers_name (name)
) ENGINE=InnoDB;

CREATE TABLE suppliers (
    id              INT UNSIGNED   NOT NULL AUTO_INCREMENT,
    name            VARCHAR(120)   NOT NULL,
    phone           VARCHAR(30)    NULL,
    email           VARCHAR(120)   NULL,
    address         VARCHAR(255)   NULL,
    opening_balance DECIMAL(12, 2) NOT NULL DEFAULT 0.00,
    is_active       TINYINT(1)     NOT NULL DEFAULT 1,
    created_at      DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_suppliers_name (name)
) ENGINE=InnoDB;
