# routes/auth_routes.py
from fastapi import APIRouter, Response, Depends, Request, HTTPException, status
from app.auth import login, logout, get_current_user, change_password
from app.schemas import LoginRequest, ChangePasswordRequest, SignupRequest, UserResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_db
from app.models import User, UserRole
from app.utils import hash_password

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/login")
async def login_route(
    request: Request,
    response: Response,
    data: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    return await login(request=request, response=response, username=data.username, password=data.password, db=db)


@router.post("/logout")
async def logout_route(request: Request, response: Response):
    return await logout(request, response)


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup_route(
    data: SignupRequest,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(User).filter_by(username=data.username))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already exists")

    user = User(
        username=data.username,
        password=hash_password(data.password),
        first_name=data.first_name,
        role=UserRole.LOAN_OFFICER,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/me")
async def me_route(current_user: User = Depends(get_current_user)):
    raw_role = current_user.role
    print(f"/auth/me raw role value: {raw_role!r}")
    role_value = raw_role.value if hasattr(raw_role, "value") else str(raw_role)
    return {
        "id": current_user.id,
        "username": current_user.username,
        "first_name": current_user.first_name,
        "role": role_value,
        "created_at": current_user.created_at,
    }



@router.put("/change-password")
async def change_password_route(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    return await change_password(data=data, current_user=current_user, db=db)