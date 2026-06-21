from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
import os
import base64
import tempfile
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

Base = declarative_base()


def _build_connect_args() -> dict:
    """Build connect_args for SQLAlchemy based on env vars.

    Supports MySQL (aiomysql) SSL via:
    - MYSQL_SSL_CA_B64 (base64-encoded CA bundle)
    - MYSQL_SSL_CA_PATH (filesystem path to CA bundle)
    - MYSQL_SSL (true/false) fallback

    When a base64 CA is provided we write it to a temporary file and pass
    its path to the driver so Render / Aiven deployments can use an env var
    rather than a committed file.
    """
    connect_args: dict = {}

    # Only attempt SSL handling for MySQL-style URLs
    if DATABASE_URL and DATABASE_URL.startswith("mysql"):
        ssl_ca_b64 = os.getenv("MYSQL_SSL_CA_B64") or os.getenv("AIVEN_MYSQL_SSL_CA_B64")
        ssl_ca_path = os.getenv("MYSQL_SSL_CA_PATH") or os.getenv("AIVEN_MYSQL_SSL_CA_PATH")
        ssl_flag = os.getenv("MYSQL_SSL", "true").lower() not in ("0", "false", "no")

        if ssl_ca_b64:
            try:
                ca_bytes = base64.b64decode(ssl_ca_b64)
                tf = tempfile.NamedTemporaryFile(delete=False)
                tf.write(ca_bytes)
                tf.flush()
                ca_file = tf.name
                connect_args["ssl"] = {"ca": ca_file}
            except Exception:
                # Best-effort fallback to a simple boolean SSL flag
                connect_args["ssl"] = True
        elif ssl_ca_path:
            connect_args["ssl"] = {"ca": ssl_ca_path}
        elif ssl_flag:
            connect_args["ssl"] = True

    return connect_args


_connect_args = _build_connect_args()

engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    future=True,
    connect_args=_connect_args or {},
)

AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
