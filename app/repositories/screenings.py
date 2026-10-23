import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.screening import Screening


async def get_screening(
    session: AsyncSession,
    screening_id: uuid.UUID,
    *,
    tenant_id: str | None = None,
    for_update: bool = False,
) -> Screening | None:

    stmt = select(Screening).where(Screening.id == screening_id)

    if tenant_id is not None:
        stmt = stmt.where(Screening.tenant_id == tenant_id)

    if for_update:
        stmt = stmt.with_for_update()

    return await session.scalar(stmt)


async def list_screenings(
    session: AsyncSession, *, tenant_id: str, limit: int, offset: int
) -> tuple[list[Screening], int]:

    tenant_filter = Screening.tenant_id == tenant_id

    rows = list(
        (
            await session.scalars(
                select(Screening)
                .where(tenant_filter)
                .order_by(Screening.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )

    count = int(
        await session.scalar(select(func.count()).select_from(Screening).where(tenant_filter)) or 0
    )

    return rows, count
