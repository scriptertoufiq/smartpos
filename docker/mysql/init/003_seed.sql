-- ---------------------------------------------------------------------------
-- SmartPOS - minimum data the application needs in order to start.
-- ---------------------------------------------------------------------------

SET NAMES utf8mb4;

INSERT INTO warehouses (code, name, address) VALUES
    ('MAIN', 'Main Outlet', 'Head office / shop floor');

INSERT INTO units (name, short_name) VALUES
    ('Piece',    'pcs'),
    ('Kilogram', 'kg'),
    ('Gram',     'g'),
    ('Litre',    'ltr'),
    ('Packet',   'pkt'),
    ('Box',      'box'),
    ('Dozen',    'dzn');

INSERT INTO categories (name) VALUES
    ('Uncategorised');

-- Counter sales that are not tied to a named customer use this row.
INSERT INTO customers (name, is_walk_in) VALUES
    ('Walk-in Customer', 1);

-- Default administrator.
-- password_hash below is a bcrypt hash of the literal string "admin123".
-- CHANGE THIS AFTER THE FIRST LOGIN.
INSERT INTO users (username, full_name, password_hash, role, warehouse_id) VALUES
    ('admin',
     'System Administrator',
     '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewdBPj/RQ7pJHvYy',
     'admin',
     1);

INSERT INTO settings (setting_key, setting_value) VALUES
    ('shop_name',       'SmartPOS Retail'),
    ('shop_address',    ''),
    ('shop_phone',      ''),
    ('currency_symbol', 'BDT'),
    ('default_tax_rate','0.00'),
    ('invoice_prefix',  'INV-'),
    ('invoice_footer',  'Thank you for shopping with us.');
