from typing import Annotated

from fastapi import APIRouter, Depends

from fyro.auth import User, current_user

router = APIRouter()


@router.get("/whoami")
async def whoami(user: Annotated[User, Depends(current_user)]):
    return {"username": user.username}
