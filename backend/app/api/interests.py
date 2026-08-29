"""
Carrier presses "I'm interested" on a broadcast load. Ownership is
strictly enforced: the truck referenced must belong to the user whose
telegram_id is presented (never trust a client-supplied user_id/truck_id
pairing blindly) -- see the explicit ownership check below.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_bot_secret
from app.models.models import (
    Interest, InterestStatus, Load, LoadStatus, Notification,
    NotificationStatus, NotificationType, Truck, User, UserRole,
)
from app.schemas.schemas import InterestIn
from app.services import notifications
from app.services.audit import log_action
from app.services.matching import MatchResult, create_match_safely

router = APIRouter(prefix="/internal/interests", tags=["interests"], dependencies=[Depends(verify_bot_secret)])


@router.post("")
async def register_interest(payload: InterestIn, db: AsyncSession = Depends(get_db)):
    user_result = await db.execute(select(User).where(User.telegram_id == payload.telegram_id))
    user = user_result.scalar_one_or_none()
    if user is None or user.role != UserRole.CARRIER:
        raise HTTPException(status_code=403, detail="غير مصرح")

    truck_result = await db.execute(select(Truck).where(Truck.id == payload.truck_id, Truck.is_deleted.is_(False)))
    truck = truck_result.scalar_one_or_none()
    if truck is None or truck.user_id != user.id:
        # Ownership check: a user can only express interest via THEIR OWN truck.
        raise HTTPException(status_code=403, detail="لا تملك هذه الشاحنة")

    load_result = await db.execute(select(Load).where(Load.id == payload.load_id, Load.is_deleted.is_(False)))
    load = load_result.scalar_one_or_none()
    if load is None:
        raise HTTPException(status_code=404, detail="Load not found")
    if load.status in (LoadStatus.CANCELLED, LoadStatus.COMPLETED, LoadStatus.CONFIRMED):
        raise HTTPException(status_code=409, detail="هذا الطلب لم يعد متاحًا")

    interest = Interest(load_id=load.id, truck_id=truck.id, user_id=user.id, status=InterestStatus.INTERESTED)
    try:
        async with db.begin_nested():
            db.add(interest)
            await db.flush()
        interest_created = True
    except IntegrityError:
        await db.rollback()
        interest_created = False  # already registered -- idempotent no-op

    match = None
    if interest_created:
        result = MatchResult(score=60, reasons=["🚛 اهتمام مباشر من الناقل"])
        match, _ = await create_match_safely(db, load, truck, result)

        # Stop future reminders for this carrier on this load.
        notif_result = await db.execute(
            select(Notification).where(
                Notification.user_id == user.id,
                Notification.load_id == load.id,
                Notification.notification_type == NotificationType.BROADCAST,
            )
        )
        notif = notif_result.scalar_one_or_none()
        if notif is not None:
            notif.status = NotificationStatus.INTERESTED

        await log_action(db, user.id, "interest_registered", "load", load.id)
        await db.commit()

        admin_result = await db.execute(select(User).where(User.role == UserRole.ADMIN).limit(1))
        admin = admin_result.scalar_one_or_none()
        await notifications.notify_interest(db, load, truck, admin.id if admin else None)
        await db.commit()

    return {"registered": interest_created, "match_id": str(match.id) if match else None}
