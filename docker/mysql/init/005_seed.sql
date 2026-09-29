-- ---------------------------------------------------------------------------
-- SmartPOS · 005 · Seed
-- The minimum the application needs in order to start.
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

-- Counter sales not tied to a named customer use this row. The application
-- refuses to delete or un-flag it.
INSERT INTO customers (name, is_walk_in) VALUES
    ('Walk-in Customer', 1);

-- Default administrator.
-- password_hash is a real bcrypt hash (cost 12) of the string "admin123",
-- generated with bcrypt and verified. Do not hand-edit it.
-- CHANGE THIS AFTER THE FIRST LOGIN.
INSERT INTO users (username, full_name, password_hash, role, warehouse_id) VALUES
    ('admin',
     'System Administrator',
     '$2b$12$s6NMuA4cjIidjSJZHpVvlekJrilgR5Y6cc2H.wE1eQJ.bQ3q5IwFG',
     'admin',
     1);

-- Numbers reset each year (design decision 8.1), so `period` holds the year.
-- The application creates the next year's row on first use in that year.
INSERT INTO document_sequences (doc_type, period, prefix, next_number, padding) VALUES
    ('sale',        YEAR(CURDATE()), 'INV-', 1, 6),
    ('purchase',    YEAR(CURDATE()), 'PUR-', 1, 6),
    ('sale_return', YEAR(CURDATE()), 'RET-', 1, 6),
    ('transfer',    YEAR(CURDATE()), 'TRF-', 1, 6);

INSERT INTO settings (setting_key, setting_value) VALUES
    ('shop_name',        'SmartPOS Retail'),
    ('shop_address',     ''),
    ('shop_phone',       ''),
    ('currency_symbol',  'BDT'),
    ('default_tax_rate', '0.00'),
    ('invoice_footer',   'Thank you for shopping with us.'),
    ('idle_lock_minutes','15');
