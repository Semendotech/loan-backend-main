from logging.config import fileConfig
from sqlalchemy import pool, create_engine
from sqlalchemy.engine import Connection
import os
import sys
from alembic import context
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

load_dotenv()

from app.database import Base
from app import models

config = context.config

if config.config_file_name is not None:
	fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url():
	"""Get database URL from environment variable, forced to sync pymysql driver"""
	database_url = os.getenv("DATABASE_URL")
	if not database_url:
		raise ValueError("DATABASE_URL environment variable is not set")
	# Strip query params (ssl=true etc.) - we'll pass SSL via connect_args instead
	database_url = database_url.split("?")[0]
	# Force sync pymysql driver regardless of what's in the URL (aiomysql, asyncpg, etc.)
	if "+aiomysql" in database_url:
		database_url = database_url.replace("+aiomysql", "+pymysql")
	elif "+asyncpg" in database_url:
		database_url = database_url.replace("+asyncpg", "")
	return database_url


def run_migrations_offline() -> None:
	"""Run migrations in 'offline' mode."""
	url = get_url()
	context.configure(
		url=url,
		target_metadata=target_metadata,
		literal_binds=True,
		dialect_opts={"paramstyle": "named"},
	)
	with context.begin_transaction():
		context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
	context.configure(connection=connection, target_metadata=target_metadata)
	with context.begin_transaction():
		context.run_migrations()


def run_migrations_online() -> None:
	"""Run migrations in 'online' mode using a sync engine."""
	url = get_url()
	connectable = create_engine(
		url,
		poolclass=pool.NullPool,
		connect_args={"ssl": {"ssl": True}},
	)
	with connectable.connect() as connection:
		do_run_migrations(connection)
	connectable.dispose()


if context.is_offline_mode():
	run_migrations_offline()
else:
	run_migrations_online()