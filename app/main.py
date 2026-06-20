import os
import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text, select
from alembic import command
from alembic.config import Config

from app.database import engine, Base, AsyncSessionLocal
from app import models
from app.utils import hash_password
from app.routes import auth_routes, customer_routes, loan_routes, dashboard_routes, payment_routes, arrears_routes, mpesa_routes
from app.routes.user_routes import router as user_routes

app = FastAPI(title="Loan Management System")

origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
allow_origin_regex = r"^https?://([a-zA-Z0-9-]+\.)?vercel\.app$"

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# routers
app.include_router(auth_routes.router)
app.include_router(user_routes)
app.include_router(customer_routes.router)
app.include_router(loan_routes.router)
app.include_router(dashboard_routes.router)
app.include_router(payment_routes.router)
app.include_router(arrears_routes.router)
app.include_router(mpesa_routes.router)


def _run_alembic_migrations() -> None:
    migrations_dir = Path(__file__).resolve().parents[1] / "alembic"
    config_path = migrations_dir / "alembic.ini"
    if not config_path.exists():
        raise FileNotFoundError(f"Alembic config not found at {config_path}")

    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set. Set the environment variable before startup.")

    alembic_cfg = Config(str(config_path))
    alembic_cfg.set_main_option("script_location", str(migrations_dir))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    print(f"🔧 Alembic migration target URL configured: {'yes' if db_url else 'no'}")
    command.upgrade(alembic_cfg, "head")

@app.on_event("startup")
async def startup_event():
    try:
        db_url = os.getenv("DATABASE_URL", "")
        print(f"🔧 DATABASE_URL configured: {'yes' if db_url else 'no'}")
        print(f"🔧 database backend: {engine.url.get_backend_name()}")

        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            print("✅ Database connection successful.")

        await asyncio.to_thread(_run_alembic_migrations)
        print("✅ Alembic migrations applied.")

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(models.User).filter_by(username="admin"))
            user = result.scalar_one_or_none()

            if not user:
                new_user = models.User(
                    username="admin",
                    first_name="Admin",
                    password=hash_password("Admin@123"),
                    role=models.UserRole.ADMIN,
                )
                session.add(new_user)
                await session.commit()
                print("✅ Admin user created with username='admin' and password='Admin@123'")
            else:
                print("Admin user already exists, skipping seed.")
    except Exception as e:
        print("❌ Startup error:", e)


@app.on_event("shutdown")
async def shutdown_event():
    await engine.dispose()
    print("🛑 Database connection closed.")


@app.get("/")
async def root():
    return {"message": "Server and database are running successfully!"}