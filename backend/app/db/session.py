from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def create_session_factory(dsn: str):
    engine = create_engine(dsn, future=True)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
