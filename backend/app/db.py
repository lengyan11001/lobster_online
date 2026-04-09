from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .core.config import settings

_db_url = (settings.database_url or "").strip()
if "sqlite" in _db_url.lower():
    engine = create_engine(
        _db_url,
        connect_args={"check_same_thread": False},
    )
else:
    # async 路由里若长时间 await 仍持有 Session，会占满池子（默认 5+10）；提高下限并缩短占用时间见 assets.save-url
    engine = create_engine(
        _db_url,
        pool_pre_ping=True,
        pool_size=max(10, int(settings.db_pool_size)),
        max_overflow=max(15, int(settings.db_max_overflow)),
        pool_timeout=max(60, int(settings.db_pool_timeout)),
        pool_recycle=max(60, int(settings.db_pool_recycle)),
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
