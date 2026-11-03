"""Admin actions an extension can refuse through ``register_action_check``."""

import uuid
from dataclasses import dataclass, replace

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.extensions.registry import get_registry
from src.models.organisation import Organisation
from src.models.site import Site
from src.schemas.auth import CurrentUser
from src.services.dependencies import get_current_user


@dataclass(frozen=True)
class ActionContext:
    organisation_id: uuid.UUID | None
    action: str
    site_id: uuid.UUID | None = None
    method: str | None = None


class ActionRefusedError(Exception):
    """A check refused the action. ``str()`` is the reason."""


async def ensure_allowed(db: AsyncSession, ctx: ActionContext, *, lock: bool = False) -> None:
    """Raise ``ActionRefusedError`` if a registered check refuses ``ctx``.

    A missing ``organisation_id`` is looked up from ``site_id``. ``lock``
    holds the organisation row to the end of the transaction, so a check
    that counts things can't be raced by a parallel request.
    """
    checks = get_registry().action_checks
    if checks and ctx.organisation_id is None:
        org_id = await db.scalar(select(Site.organisation_id).where(Site.id == ctx.site_id))
        ctx = replace(ctx, organisation_id=org_id)
    if lock and checks:
        await db.execute(
            select(Organisation.id).where(Organisation.id == ctx.organisation_id).with_for_update()
        )
    for check in checks:
        if reason := await check(db, ctx):
            raise ActionRefusedError(reason)


def guard_writes(area: str) -> Depends:
    """Router dependency that checks ``<area>.write`` on every non-GET request."""

    async def _guard(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        if request.method in {"GET", "HEAD", "OPTIONS"} or not get_registry().action_checks:
            return
        try:
            site_id = uuid.UUID(request.path_params["site_id"])
        except (KeyError, ValueError):
            site_id = None
        if site_id and not await db.scalar(
            select(Site.id).where(Site.id == site_id, Site.organisation_id == user.organisation_id)
        ):
            return  # Not this organisation's site: the route itself answers 404.
        ctx = ActionContext(user.organisation_id, f"{area}.write", site_id, request.method)
        await ensure_allowed(db, ctx)

    return Depends(_guard)
