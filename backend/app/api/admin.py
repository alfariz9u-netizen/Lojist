"""
Admin panel, operated entirely from Telegram (/admin command in the bot).
Every endpoint here requires the caller to resolve, server-side, to a
User row with role == ADMIN -- never derived from a Telegram username,
never settable by the user themselves (see core/security.py and
api/users.py's bootstrap-only promotion).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_bot_secret
from app.models.models import (
    Load, LoadStatus, Match, MatchStatus, Notification, Truck, TruckStatus, User, UserRole,
)
from app.schemas.schemas import AdminActionIn
from app.services import notifications
from app.services.audit import log_action
from app.services.trusted_pairs import mark_trusted

router = APIRouter(prefix="/internal/admin", tags=["admin"], dependencies=[Depends(verify_bot_secret)])


async def _require_admin(db: AsyncSession, telegram_id: str) -> User:
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None or user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/overview")
async def overview(telegram_id: str, db: AsyncSession = Depends(get_db)):
    await _require_admin(db, telegram_id)

    open_loads = (await db.execute(
        select(func.count()).select_from(Load).where(
            Load.is_deleted.is_(False),
            Load.status.in_([LoadStatus.NEW, LoadStatus.WAITING_FOR_MATCH]),
        )
    )).scalar_one()
    available_trucks = (await db.execute(
        select(func.count()).select_from(Truck).where(
            Truck.is_deleted.is_(False), Truck.status == TruckStatus.AVAILABLE, Truck.available.is_(True)
        )
    )).scalar_one()
    new_matches = (await db.execute(
        select(func.count()).select_from(Match).where(Match.status == MatchStatus.PENDING)
    )).scalar_one()
    waiting = (await db.execute(
        select(func.count()).select_from(Load).where(Load.status == LoadStatus.WAITING_FOR_MATCH)
    )).scalar_one()

    return {
        "open_loads": open_loads, "available_trucks": available_trucks,
        "new_matches": new_matches, "waiting_for_match": waiting,
    }


@router.get("/matches")
async def list_matches(telegram_id: str, db: AsyncSession = Depends(get_db)):
    await _require_admin(db, telegram_id)
    result = await db.execute(
        select(Match).where(Match.status.in_([MatchStatus.PENDING, MatchStatus.INTERESTED])).limit(50)
    )
    matches = result.scalars().all()
    out = []
    for m in matches:
        load = await db.get(Load, m.load_id)
        truck = await db.get(Truck, m.truck_id)
        out.append({
            "match_id": str(m.id), "status": m.status.value, "score": m.score,
            "load": {"id": str(load.id), "origin": load.origin_city, "destination": load.destination_city},
            "truck": {"id": str(truck.id), "current_city": truck.current_city},
        })
    return out


@router.post("/action")
async def take_action(payload: AdminActionIn, db: AsyncSession = Depends(get_db)):
    admin = await _require_admin(db, payload.telegram_id)

    if payload.action == "contact":
        if not payload.match_id:
            raise HTTPException(status_code=400, detail="match_id required")
        match = await db.get(Match, payload.match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found")
        load = await db.get(Load, match.load_id)
        truck = await db.get(Truck, match.truck_id)
        shipper = await db.get(User, load.user_id)
        carrier = await db.get(User, truck.user_id)
        match.status = MatchStatus.ADMIN_CONTACTING
        load.status = LoadStatus.ADMIN_CONTACTING
        await log_action(db, admin.id, "admin_viewed_contact", "match", match.id)
        await db.commit()
        return {
            "shipper": {"name": shipper.name, "phone": shipper.phone},
            "carrier": {"name": carrier.name, "phone": carrier.phone},
        }

    if payload.action == "connect":
        match = await db.get(Match, payload.match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found")
        load = await db.get(Load, match.load_id)
        truck = await db.get(Truck, match.truck_id)
        match.status = MatchStatus.CONNECTED
        load.status = LoadStatus.CONNECTED
        # This shipper/carrier pair is now vetted -- any FUTURE match
        # between the same two people will auto-connect without
        # needing the admin to manually approve it again. See
        # services/trusted_pairs.py.
        await mark_trusted(db, load.user_id, truck.user_id)
        await log_action(db, admin.id, "admin_connected_users", "match", match.id)
        await db.commit()
        return {"status": "connected"}

    if payload.action == "reject":
        match = await db.get(Match, payload.match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found")
        load = await db.get(Load, match.load_id)
        match.status = MatchStatus.REJECTED
        if load.status not in (LoadStatus.CONNECTED, LoadStatus.CONFIRMED, LoadStatus.COMPLETED):
            load.status = LoadStatus.WAITING_FOR_MATCH
        await log_action(db, admin.id, "match_cancelled", "match", match.id)
        await db.commit()
        return {"status": "rejected"}

    if payload.action == "cancel_load":
        load = await db.get(Load, payload.load_id)
        if load is None:
            raise HTTPException(status_code=404, detail="Load not found")
        load.status = LoadStatus.CANCELLED
        await log_action(db, admin.id, "admin_cancelled_load", "load", load.id)
        await db.commit()
        return {"status": "cancelled"}

    if payload.action == "resend_notification":
        match = await db.get(Match, payload.match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found")
        load = await db.get(Load, match.load_id)
        truck = await db.get(Truck, match.truck_id)
        await notifications.notify_match_found(db, load, truck, match, admin.id)
        await log_action(db, admin.id, "notification_resent", "match", match.id)
        await db.commit()
        return {"status": "resent"}

    raise HTTPException(status_code=400, detail="Unknown action")
