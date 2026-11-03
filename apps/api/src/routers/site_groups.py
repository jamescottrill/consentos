import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.site import Site
from src.models.site_group import SiteGroup
from src.schemas.auth import CurrentUser
from src.schemas.site_group import SiteGroupCreate, SiteGroupResponse, SiteGroupUpdate
from src.services.action_checks import guard_writes
from src.services.dependencies import require_role

router = APIRouter(
    prefix="/site-groups", tags=["site-groups"], dependencies=[guard_writes("site_group")]
)


@router.post("/", response_model=SiteGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_site_group(
    body: SiteGroupCreate,
    current_user: CurrentUser = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new site group within the current organisation."""
    # Check name uniqueness within the org
    existing = await db.execute(
        select(SiteGroup).where(
            SiteGroup.organisation_id == current_user.organisation_id,
            SiteGroup.name == body.name,
            SiteGroup.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Site group '{body.name}' already exists in this organisation",
        )

    group = SiteGroup(
        organisation_id=current_user.organisation_id,
        name=body.name,
        description=body.description,
    )
    db.add(group)
    await db.flush()
    await db.refresh(group)
    return _to_response(group, site_count=0)


@router.get("/", response_model=list[SiteGroupResponse])
async def list_site_groups(
    current_user: CurrentUser = Depends(require_role("owner", "admin", "editor", "viewer")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List all site groups in the current organisation with site counts."""
    # Subquery for site counts
    site_count_sq = (
        select(
            Site.site_group_id,
            func.count(Site.id).label("cnt"),
        )
        .where(Site.deleted_at.is_(None))
        .group_by(Site.site_group_id)
        .subquery()
    )

    result = await db.execute(
        select(SiteGroup, func.coalesce(site_count_sq.c.cnt, 0).label("site_count"))
        .outerjoin(site_count_sq, SiteGroup.id == site_count_sq.c.site_group_id)
        .where(
            SiteGroup.organisation_id == current_user.organisation_id,
            SiteGroup.deleted_at.is_(None),
        )
        .order_by(SiteGroup.name)
    )

    return [_to_response(row.SiteGroup, site_count=row.site_count) for row in result.all()]


@router.get("/{group_id}", response_model=SiteGroupResponse)
async def get_site_group(
    group_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_role("owner", "admin", "editor", "viewer")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get a specific site group by ID."""
    group = await _get_org_group(group_id, current_user.organisation_id, db)
    site_count = await _count_sites(group_id, db)
    return _to_response(group, site_count=site_count)


@router.patch("/{group_id}", response_model=SiteGroupResponse)
async def update_site_group(
    group_id: uuid.UUID,
    body: SiteGroupUpdate,
    current_user: CurrentUser = Depends(require_role("owner", "admin", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update a site group's name or description."""
    group = await _get_org_group(group_id, current_user.organisation_id, db)

    update_data = body.model_dump(exclude_unset=True)

    # Check name uniqueness if name is being changed
    if "name" in update_data and update_data["name"] != group.name:
        existing = await db.execute(
            select(SiteGroup).where(
                SiteGroup.organisation_id == current_user.organisation_id,
                SiteGroup.name == update_data["name"],
                SiteGroup.deleted_at.is_(None),
                SiteGroup.id != group_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Site group '{update_data['name']}' already exists",
            )

    for field, value in update_data.items():
        setattr(group, field, value)

    await db.flush()
    await db.refresh(group)
    site_count = await _count_sites(group_id, db)
    return _to_response(group, site_count=site_count)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site_group(
    group_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a site group. Sites in this group become ungrouped."""
    group = await _get_org_group(group_id, current_user.organisation_id, db)

    # Ungroup all sites in this group
    result = await db.execute(
        select(Site).where(
            Site.site_group_id == group_id,
            Site.deleted_at.is_(None),
        )
    )
    for site in result.scalars().all():
        site.site_group_id = None

    group.deleted_at = datetime.now(UTC)
    await db.flush()


# ── Helpers ──────────────────────────────────────────────────────────


async def _get_org_group(
    group_id: uuid.UUID,
    organisation_id: uuid.UUID,
    db: AsyncSession,
) -> SiteGroup:
    """Fetch a site group ensuring it belongs to the given organisation."""
    result = await db.execute(
        select(SiteGroup).where(
            SiteGroup.id == group_id,
            SiteGroup.organisation_id == organisation_id,
            SiteGroup.deleted_at.is_(None),
        )
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Site group not found",
        )
    return group


async def _count_sites(group_id: uuid.UUID, db: AsyncSession) -> int:
    """Count active sites in a group."""
    result = await db.execute(
        select(func.count(Site.id)).where(
            Site.site_group_id == group_id,
            Site.deleted_at.is_(None),
        )
    )
    return result.scalar_one()


def _to_response(group: SiteGroup, *, site_count: int) -> dict:
    """Convert a SiteGroup model to a response dict with site_count."""
    return {
        "id": group.id,
        "organisation_id": group.organisation_id,
        "name": group.name,
        "description": group.description,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
        "site_count": site_count,
    }
