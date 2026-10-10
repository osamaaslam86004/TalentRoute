import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.screening import Screening


async def get_screening(session: AsyncSession, screening_id: uuid.UUID, *, for_update: bool = False) -> Screening | None:
    stmt = select(Screening).where(Screening.id == screening_id)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def list_screenings(session: AsyncSession, *, limit: int, offset: int) -> tuple[list[Screening], int]:
    rows = list((await session.scalars(
        select(Screening).order_by(Screening.created_at.desc()).limit(limit).offset(offset)
    )).all())
    # For the current bounded API, use a separate count query for correct pagination metadata.
    from sqlalchemy import func
    count = int(await session.scalar(select(func.count()).select_from(Screening)) or 0)
    return rows, count
