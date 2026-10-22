from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from fyro.auth import sessions
from fyro.auth.models import User

router = APIRouter(tags=["auth"])
CurrentUser = Annotated[User, Depends(sessions.current_user)]


class Credentials(BaseModel):
    username: str
    password: str


class LoginResult(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_at: datetime


@router.post("/login", response_model=LoginResult)
async def login(credentials: Credentials, response: Response) -> LoginResult:
    user = await sessions.authenticate(credentials.username, credentials.password)
    if user is None:
        raise HTTPException(401, "Invalid username or password.")
    token, expires_at = await sessions.create_session(user)
    sessions.set_cookie(response, token)
    return LoginResult(token=token, expires_at=expires_at)


@router.post("/logout", status_code=204)
async def logout(request: Request, user: CurrentUser) -> Response:
    await sessions.end_session(request)
    response = Response(status_code=204)
    sessions.clear_cookie(response)
    return response


@router.get("/me", response_model=User.Read)
async def me(user: CurrentUser) -> object:
    return User.Read.model_validate(user, from_attributes=True)
