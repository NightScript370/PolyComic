"""Request dependencies: who is signed in.

The session cookie (Starlette ``SessionMiddleware``, signed with SESSION_SECRET) holds
only the numeric user id under ``uid``. The user row is loaded per request, so deleting
a user ends their sessions.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import ApiError
from app.db import repo
from app.db.models import User
from app.db.session import get_session
from app.logging_setup import user_id_var

SESSION_KEY = "uid"
SIGN_IN_MESSAGE = "Please sign in."

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def optional_user(request: Request, session: DbSession) -> User | None:
    uid = request.session.get(SESSION_KEY)
    if not isinstance(uid, int):
        return None
    user = await repo.get_user(session, uid)
    if user is None:
        request.session.clear()
        return None
    user_id_var.set(user.id)
    return user


async def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise ApiError(401, SIGN_IN_MESSAGE)
    return user


CurrentUser = Annotated[User, Depends(current_user)]
OptionalUser = Annotated[User | None, Depends(optional_user)]


def user_json(user: User | None) -> dict | None:
    if user is None:
        return None
    return {"id": user.id, "name": user.name, "email": user.email, "picture": user.picture}
