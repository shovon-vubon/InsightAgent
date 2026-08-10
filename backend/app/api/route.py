"""A route class that commits the request's transaction before replying.

**The bug this exists to fix.** `get_db` is a dependency with `yield`, and FastAPI
runs the teardown of such a dependency *after* the response has been sent. With
the commit living in that teardown, the client could receive `201 Created` and
issue its next request before the transaction had actually committed. Measured
against the running stack, `POST /auth/register` followed immediately by
`POST /auth/login` failed **5 times out of 5**; inserting a 50 ms pause made it
pass 4 out of 4. The same race applies to any write followed by a read — creating
a conversation and then posting to it, uploading a document and then listing.

The fix is to commit inside the route handler, which runs strictly before the
response leaves. `get_db` still rolls back on failure and still closes the
session, so the unit-of-work guarantee is unchanged — only its ordering relative
to the response is now defined.

Applied once on the API router, so every endpoint gets it without any of them
having to remember.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute


class CommittingRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original_handler(request)

            # Only set when the endpoint actually depended on a session.
            session = getattr(request.state, "db_session", None)
            if session is not None and session.in_transaction():
                await session.commit()

            return response

        return handler
