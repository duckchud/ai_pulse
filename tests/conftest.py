import pytest

from db import connect, migrate


@pytest.fixture
def temporary_db(tmp_path):
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    yield conn
    conn.close()
