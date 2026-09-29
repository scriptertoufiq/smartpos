"""M0 acceptance tests.

The criteria from docs/PLAN.md, written as tests:

  * `python -m smartpos` starts and exits with no traceback
  * an exception inside `transaction()` leaves the database unchanged
  * wrong DB credentials produce a readable error, not a connector stack trace
  * the pool survives 100 sequential borrow/return cycles without leaking

Tests that need MySQL skip themselves when it is unreachable, so the suite
still runs on a machine with no database.
"""

from __future__ import annotations


import pytest

from smartpos.config import DatabaseSettings, Settings, load_settings, set_settings
from smartpos.core.exceptions import ConfigurationError, DatabaseError
from smartpos.db import connection


# --------------------------------------------------------------------- setup

@pytest.fixture(scope="module")
def db():
    """An initialised pool, or skip the test if MySQL is not reachable."""
    try:
        connection.init_pool()
        connection.check_connection()
    except DatabaseError as exc:
        pytest.skip(f"MySQL not reachable: {exc}")
    yield
    connection.close_pool()


@pytest.fixture
def scratch_table(db):
    """A table this module owns, dropped afterwards. Never touches real data."""
    name = "zz_m0_scratch"
    with connection.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {name}")
        cur.execute(f"CREATE TABLE {name} (id INT PRIMARY KEY, note VARCHAR(40)) ENGINE=InnoDB")
        conn.commit()
        cur.close()
    yield name
    with connection.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {name}")
        conn.commit()
        cur.close()


def _row_count(table: str) -> int:
    with connection.get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return cur.fetchone()[0]
        finally:
            cur.close()


# -------------------------------------------------------------------- config

def test_settings_load_from_env_file():
    s = load_settings()
    assert s.database.host
    assert s.database.name
    assert s.database.pool_size >= 1
    assert s.log_dir.is_absolute()
    assert s.backup_dir.is_absolute()


def test_dsn_never_contains_the_password():
    s = load_settings()
    assert s.database.password not in s.database.dsn or s.database.password == ""


def test_bad_log_level_is_rejected(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "CHATTY")
    with pytest.raises(ConfigurationError, match="LOG_LEVEL"):
        load_settings()


def test_bad_pool_size_is_rejected(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "500")
    with pytest.raises(ConfigurationError, match="DB_POOL_SIZE"):
        load_settings()


def test_non_numeric_port_is_rejected(monkeypatch):
    monkeypatch.setenv("DB_PORT", "three-thousand")
    with pytest.raises(ConfigurationError, match="DB_PORT"):
        load_settings()


# ---------------------------------------------------------------- the pool

def test_pool_survives_100_cycles(db):
    """Criterion: 100 sequential borrow/return cycles without leaking."""
    for _ in range(100):
        with connection.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchall()
            cur.close()
    # A leak shows up as the 101st borrow blocking or raising.
    assert connection.check_connection() is True


def test_connection_is_returned_even_when_the_block_raises(db):
    for _ in range(20):
        with pytest.raises(RuntimeError):
            with connection.get_connection():
                raise RuntimeError("boom")
    assert connection.check_connection() is True


def test_using_the_pool_before_init_is_a_readable_error(db):
    connection.close_pool()
    try:
        with pytest.raises(DatabaseError, match="not initialised"):
            with connection.get_connection():
                pass
    finally:
        # Restore it: the module fixture opened this pool and later tests need it.
        connection.init_pool()


# ------------------------------------------------------------- transactions

def test_exception_inside_transaction_leaves_the_database_unchanged(scratch_table):
    """Criterion: a failed transaction writes nothing."""
    assert _row_count(scratch_table) == 0

    with pytest.raises(RuntimeError):
        with connection.transaction() as conn:
            cur = conn.cursor()
            cur.execute(f"INSERT INTO {scratch_table} (id, note) VALUES (1, 'first')")
            cur.execute(f"INSERT INTO {scratch_table} (id, note) VALUES (2, 'second')")
            cur.close()
            raise RuntimeError("something went wrong half way through")

    assert _row_count(scratch_table) == 0, "rollback did not happen"


def test_successful_transaction_commits(scratch_table):
    with connection.transaction() as conn:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO {scratch_table} (id, note) VALUES (1, 'kept')")
        cur.close()
    assert _row_count(scratch_table) == 1


def test_a_constraint_violation_rolls_back_the_whole_transaction(scratch_table):
    """The second insert violates the primary key; neither row may survive."""
    with pytest.raises(Exception):
        with connection.transaction() as conn:
            cur = conn.cursor()
            cur.execute(f"INSERT INTO {scratch_table} (id, note) VALUES (7, 'a')")
            cur.execute(f"INSERT INTO {scratch_table} (id, note) VALUES (7, 'duplicate')")
            cur.close()
    assert _row_count(scratch_table) == 0


# --------------------------------------------------- credentials and errors

def test_wrong_password_gives_a_readable_message(db):
    """Criterion: no raw connector stack trace reaches the user."""
    real = load_settings()
    broken = Settings(
        env=real.env,
        log_level=real.log_level,
        database=DatabaseSettings(
            host=real.database.host,
            port=real.database.port,
            name=real.database.name,
            user=real.database.user,
            password="definitely-not-the-password",
            pool_size=2,
        ),
        backup_dir=real.backup_dir,
        log_dir=real.log_dir,
        currency_symbol=real.currency_symbol,
        default_tax_rate=real.default_tax_rate,
        mysqldump_path=real.mysqldump_path,
        mysql_client_path=real.mysql_client_path,
    )
    connection.close_pool()
    set_settings(broken)
    try:
        with pytest.raises(DatabaseError) as err:
            connection.init_pool()
        message = str(err.value)
        assert "DB_USER" in message or "DB_PASSWORD" in message or "refused" in message
        assert "Traceback" not in message
    finally:
        connection.close_pool()
        set_settings(None)
        connection.init_pool()


def test_unknown_database_names_the_fix(db):
    real = load_settings()
    broken = Settings(
        env=real.env, log_level=real.log_level,
        database=DatabaseSettings(
            host=real.database.host, port=real.database.port,
            name="smartpos_does_not_exist", user=real.database.user,
            password=real.database.password, pool_size=2),
        backup_dir=real.backup_dir, log_dir=real.log_dir,
        currency_symbol=real.currency_symbol, default_tax_rate=real.default_tax_rate,
        mysqldump_path=real.mysqldump_path, mysql_client_path=real.mysql_client_path,
    )
    connection.close_pool()
    set_settings(broken)
    try:
        with pytest.raises(DatabaseError) as err:
            connection.init_pool()
        assert "smartpos_does_not_exist" in str(err.value)
    finally:
        connection.close_pool()
        set_settings(None)
        connection.init_pool()


# ------------------------------------------------------------------ the CLI

def test_main_check_flag_exits_zero(db):
    from smartpos.main import main

    connection.close_pool()
    try:
        assert main(["--check"]) == 0
    finally:
        connection.close_pool()
        connection.init_pool()
