from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str
    password: str

    model_config = {"json_schema_extra": {"example": {
        "email": "user@example.com",
        "name": "Ivan Petrov",
        "password": "secret123"
    }}}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    model_config = {"json_schema_extra": {"example": {
        "email": "user@example.com",
        "password": "secret123"
    }}}


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    created_at: str


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: UserOut


class TokenResponse(BaseModel):
    access_token: str
