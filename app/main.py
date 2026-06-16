from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text, select
from app.database import engine, Base, AsyncSessionLocal
from app import models
from app.utils import hash_password
from app.routes import auth_routes, customer_routes, loan_routes, dashboard_routes, payment_routes, arrears_routes, mpesa_routes

app = FastAPI(title="Loan Management System")

origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://loan-ui-bay.vercel.app",
    "https://semedo-loan-ui.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# routers
app.include_router(auth_routes.router)
app.include_router(customer_routes.router)
app.include_router(loan_routes.router)
app.include_router(dashboard_routes.router)
app.include_router(payment_routes.router)
app.include_router(arrears_routes.router)
app.include_router(mpesa_routes.router)


@app.on_event("startup")
async def startup_event():
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            print("✅ Database connection successful.")
            await conn.run_sync(Base.metadata.create_all)
            print("✅ Tables created or already exist.")

            try:
                await conn.execute(text("ALTER TABLE loans MODIFY COLUMN customer_id VARCHAR(30) NOT NULL"))
            except Exception:
                pass
            try:
                await conn.execute(text("ALTER TABLE loans DROP FOREIGN KEY loans_ibfk_1"))
            except Exception:
                pass
            try:
                await conn.execute(text("ALTER TABLE loans ADD CONSTRAINT fk_loans_customer_id FOREIGN KEY (customer_id) REFERENCES customers(id_number)"))
            except Exception:
                pass
            try:
                await conn.execute(text("ALTER TABLE customers DROP COLUMN address"))
                print("✅ Dropped 'address' column from customers")
            except Exception:
                pass
            try:
                await conn.execute(text("UPDATE customers SET id_number = CONCAT('MISSING-', id) WHERE id_number IS NULL"))
                await conn.execute(text("ALTER TABLE customers MODIFY COLUMN id_number VARCHAR(30) NOT NULL"))
                print("✅ Enforced NOT NULL on 'id_number' for customers")
            except Exception:
                pass

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(models.User).filter_by(username="admin"))
            user = result.scalar_one_or_none()

            if not user:
                new_user = models.User(
                    username="admin",
                    password=hash_password("Admin@123")
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