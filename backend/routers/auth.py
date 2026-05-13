from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas.auth import (
    RegisterRequest, LoginRequest, RefreshRequest,
    LogoutRequest, AuthResponse, TokenResponse, UserOut,
)
from app.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        created_at=user.created_at.isoformat(),
    )


@router.post("/register", response_model=AuthResponse, status_code=201,
             summary="Регистрация нового пользователя")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if len(body.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "WEAK_PASSWORD", "message": "Пароль должен быть не менее 8 символов"},
        )
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "EMAIL_TAKEN", "message": "Email уже зарегистрирован"},
        )

    user = User(
        email=body.email,
        name=body.name,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return AuthResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        user=_user_out(user),
    )


@router.post("/login", response_model=AuthResponse,
             summary="Вход по email и паролю")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_CREDENTIALS", "message": "Неверный email или пароль"},
        )

    return AuthResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        user=_user_out(user),
    )


@router.post("/refresh", response_model=TokenResponse,
             summary="Обновить access_token по refresh_token")
def refresh(body: RefreshRequest):
    user_id = decode_token(body.refresh_token, expected_type="refresh")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Refresh token недействителен или истёк"},
        )
    return TokenResponse(access_token=create_access_token(user_id))


@router.post("/logout", status_code=204,
             summary="Выход (клиент должен удалить токены локально)")
def logout(body: LogoutRequest):
    # Токены stateless — сервер не хранит refresh tokens.
    # Реальная инвалидация: добавить таблицу revoked_tokens (post-MVP).
    return None
