import pytest

from cfbmodel import db


@pytest.fixture
def con():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()
