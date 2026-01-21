from shared.db import get_db_session


def get_db():
    yield from get_db_session()
