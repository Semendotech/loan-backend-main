from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

Base = declarative_base()

import ssl as ssl_module

ssl_ctx = ssl_module.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl_module.CERT_NONE

engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    future=True,
    connect_args={"ssl": ssl_ctx},
)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ============ SYNC ENGINE (for legacy sync routes like dashboard_routes.py) ============
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SyncSession

def _build_sync_url(async_url: str) -> str:
    url = async_url.split("?")[0]
    if "+aiomysql" in url:
        url = url.replace("+aiomysql", "+pymysql")
    return url

sync_engine = create_engine(
    _build_sync_url(DATABASE_URL),
    echo=False,
    connect_args={"ssl": {"ssl": True}},
)
SyncSessionLocal = sessionmaker(bind=sync_engine, class_=SyncSession, expire_on_commit=False)

def get_sync_db():
    db = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()
