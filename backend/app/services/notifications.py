"""
Notification creation + delivery. Idempotent by design: the
(user_id, load_id, notification_type) unique constraint on the
`notifications` table (see models.py) is the actual guarantee against
duplicates -- not an in-memory check -- so this is safe even if called
concurrently or after a process restart/retry.

Never reveals phone numbers or other contact details in a regular
MATCH_FOUND/BROADCAST/REMINDER/PROACTIVE_MATCH notification. The ONE
exception is AUTO_CONNECTED (see notify_auto_connected below), sent only
when the two parties are already a TrustedPair -- i.e. they've already
exchanged contact info once, under admin supervision -- so it isn't a
new exposure. Otherwise, contact info is only ever shown through the
admin panel's audited "contact" action (api/admin.py).
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Load, LoadStatus, Match, MatchStatus, Notification, NotificationStatus,
    NotificationType, Truck, User, UserRole,
)
from app.services import telegram_client
from app.services.audit import log_action
from app.services.trusted_pairs import is_trusted_pair

logger = logging.getLogger("freightai")


async def _create_notification_row(
    db: AsyncSession, user_id: uuid.UUID, load_id: uuid.UUID, notification_type: NotificationType,
    truck_id: uuid.UUID | None = None, match_id: uuid.UUID | None = None,
) -> tuple[Notification, bool]:
    existing = await db.execute(
        select(Notification).where(
            Notification.user_id == user_id,
            Notification.load_id == load_id,
            Notification.notification_type == notification_type,
        )
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        return row, False

    notif = Notification(
        user_id=user_id, load_id=load_id, truck_id=truck_id, match_id=match_id,
        notification_type=notification_type, status=NotificationStatus.SENT,
    )
    try:
        async with db.begin_nested():
            db.add(notif)
            await db.flush()
        return notif, True
    except IntegrityError:
        await db.rollback()
        existing = await db.execute(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.load_id == load_id,
                Notification.notification_type == notification_type,
            )
        )
        return existing.scalar_one(), False


async def _deliver(db: AsyncSession, notif: Notification, chat_id: str, text: str, reply_markup: dict | None = None) -> None:
    ok = await telegram_client.send_message(chat_id, text, reply_markup)
    notif.status = NotificationStatus.SENT if ok else NotificationStatus.FAILED
    if not ok:
        logger.error("notification delivery failed: notif_id=%s user=%s type=%s", notif.id, notif.user_id, notif.notification_type)


def _interest_button(load_id: uuid.UUID) -> dict:
    return {"inline_keyboard": [[{"text": "🚛 أنا مهتم", "callback_data": f"interest:{load_id}"}]]}


async def notify_match_found(db: AsyncSession, load: Load, truck: Truck, match: Match, admin_id: uuid.UUID | None) -> None:
    """Sends the MATCH_FOUND notice to shipper, carrier, and admin. Only
    the admin message includes IDs / mediation buttons -- the two
    parties never see each other's contact info."""
    shipper_notif, created_s = await _create_notification_row(
        db, load.user_id, load.id, NotificationType.MATCH_FOUND, truck_id=truck.id, match_id=match.id
    )
    if created_s:
        shipper = await db.get(User, load.user_id)
        text = (
            "📦 تم العثور على ناقل مناسب لحمولتك\n\n"
            f"🚛 ناقل متوفر من {truck.current_city}\n"
            "سيتم التواصل معك من إدارة المنصة لإتمام التنسيق."
        )
        await _deliver(db, shipper_notif, shipper.telegram_id, text)

    carrier_notif, created_c = await _create_notification_row(
        db, truck.user_id, load.id, NotificationType.MATCH_FOUND, truck_id=truck.id, match_id=match.id
    )
    if created_c:
        carrier = await db.get(User, truck.user_id)
        text = (
            "🚛 تم العثور على حمولة مناسبة لشاحنتك\n\n"
            f"📍 التحميل: {load.origin_city}\n"
            f"📍 التفريغ: {load.destination_city}\n"
            f"📦 الحمولة: {load.truck_count} شاحنات ({load.truck_type or 'غير محدد'})\n\n"
            "سيتم التواصل معك من إدارة المنصة لإتمام التنسيق."
        )
        await _deliver(db, carrier_notif, carrier.telegram_id, text)

    if admin_id:
        admin_notif, created_a = await _create_notification_row(
            db, admin_id, load.id, NotificationType.MATCH_FOUND, truck_id=truck.id, match_id=match.id
        )
        if created_a:
            admin = await db.get(User, admin_id)
            text = (
                "🔔 MATCH جديد\n\n"
                f"الحمولة: {load.origin_city} → {load.destination_city}\n"
                f"الكمية: {load.truck_count}\n"
                f"الناقل: متوفر في {truck.current_city}\n\n"
                f"Load ID: #{str(load.id)[:8]}\n"
                f"Truck/Carrier ID: #{str(truck.id)[:8]}\n\n"
                "يرجى التواصل مع الطرفين وإتمام الربط عبر /admin."
            )
            await _deliver(db, admin_notif, admin.telegram_id, text)


async def notify_no_match_yet(db: AsyncSession, load: Load) -> None:
    notif, created = await _create_notification_row(db, load.user_id, load.id, NotificationType.BROADCAST)
    # BROADCAST here is reused as the "we're searching" ack to the shipper
    # only on first creation; the actual broadcast to carriers is separate
    # (see broadcast_load_to_carriers) keyed per-carrier.
    if created:
        shipper = await db.get(User, load.user_id)
        text = "⏳ لم نجد ناقلًا مناسبًا حاليًا.\n\nسنواصل البحث ونبلغك عند توفر ناقل مناسب."
        await _deliver(db, notif, shipper.telegram_id, text)


async def broadcast_load_to_carrier(db: AsyncSession, load: Load, carrier_user: User) -> Notification | None:
    notif, created = await _create_notification_row(db, carrier_user.id, load.id, NotificationType.BROADCAST)
    if not created:
        return None  # already notified this carrier about this load -- no duplicate
    text = (
        "🔔 حمولة جديدة\n\n"
        f"📍 من: {load.origin_city}\n"
        f"📍 إلى: {load.destination_city}\n"
        f"🚚 المطلوب: {load.truck_count} ({load.truck_type or 'أي نوع'})\n\n"
        "إذا كنت متاحًا لهذه الرحلة اضغط على الزر أدناه."
    )
    await _deliver(db, notif, carrier_user.telegram_id, text, reply_markup=_interest_button(load.id))
    return notif


async def send_reminder(db: AsyncSession, load: Load, carrier_user: User, notif: Notification) -> None:
    """Reminder reuses the SAME notification row (BROADCAST type) rather
    than creating a new one -- reminder_sent + last_sent_at track it, so
    a user is only ever reminded once, and never after they've
    interacted (see workers scheduler which filters on notif.status)."""
    if notif.reminder_sent or notif.status != NotificationStatus.SENT:
        return
    text = (
        "🔔 تذكير: حمولة بانتظار ناقل\n\n"
        f"📍 من: {load.origin_city}\n"
        f"📍 إلى: {load.destination_city}\n\n"
        "إذا كنت متاحًا اضغط أدناه."
    )
    ok = await telegram_client.send_message(carrier_user.telegram_id, text, reply_markup=_interest_button(load.id))
    if ok:
        notif.reminder_sent = True
        from datetime import datetime, timezone
        notif.last_sent_at = datetime.now(timezone.utc)


async def notify_interest(db: AsyncSession, load: Load, truck: Truck, admin_id: uuid.UUID | None) -> None:
    """Carrier pressed 'I'm interested'. Shipper is told a carrier is
    interested (no contact info); admin gets full mediation details."""
    shipper_notif, created_s = await _create_notification_row(
        db, load.user_id, load.id, NotificationType.INTEREST_RECEIVED, truck_id=truck.id
    )
    if created_s:
        shipper = await db.get(User, load.user_id)
        text = (
            "🚛 أبدى أحد الناقلين اهتمامًا بحمولتك\n\n"
            "سيتم التواصل معك من إدارة المنصة لإتمام التنسيق."
        )
        await _deliver(db, shipper_notif, shipper.telegram_id, text)

    if admin_id:
        admin_notif, created_a = await _create_notification_row(
            db, admin_id, load.id, NotificationType.INTEREST_RECEIVED, truck_id=truck.id
        )
        if created_a:
            admin = await db.get(User, admin_id)
            text = (
                f"🔔 ناقل مهتم بحمولة #{str(load.id)[:8]}\n\n"
                f"{load.origin_city} → {load.destination_city}\n"
                f"Carrier/Truck: #{str(truck.id)[:8]}\n\n"
                "يرجى المتابعة عبر /admin."
            )
            await _deliver(db, admin_notif, admin.telegram_id, text)


async def notify_proactive_match(db: AsyncSession, load: Load, truck: Truck, match: Match, admin_id: uuid.UUID | None) -> None:
    notif, created = await _create_notification_row(
        db, load.user_id, load.id, NotificationType.PROACTIVE_MATCH, truck_id=truck.id, match_id=match.id
    )
    if created:
        shipper = await db.get(User, load.user_id)
        text = (
            f"🔔 ناقل متوقع توفره في {truck.trip_destination}\n\n"
            "🚛 ناقل في طريقه وقد يكون مناسبًا لحمولتك.\n"
            f"📦 قد يكون مناسبًا لحمولتك من {load.origin_city} إلى {load.destination_city}.\n\n"
            "سيتم التواصل معك من إدارة المنصة."
        )
        await _deliver(db, notif, shipper.telegram_id, text)

    carrier_notif, created_c = await _create_notification_row(
        db, truck.user_id, load.id, NotificationType.PROACTIVE_MATCH, truck_id=truck.id, match_id=match.id
    )
    if created_c:
        carrier = await db.get(User, truck.user_id)
        text = (
            "🔔 فرصة حمولة عودة (Backhaul)\n\n"
            f"📍 من: {load.origin_city}\n📍 إلى: {load.destination_city}\n\n"
            "سيتم التواصل معك من إدارة المنصة إذا كانت مناسبة."
        )
        await _deliver(db, carrier_notif, carrier.telegram_id, text)

    if admin_id:
        admin_notif, created_a = await _create_notification_row(
            db, admin_id, load.id, NotificationType.PROACTIVE_MATCH, truck_id=truck.id, match_id=match.id
        )
        if created_a:
            admin = await db.get(User, admin_id)
            text = (
                f"🔔 Proactive Match\n\nLoad #{str(load.id)[:8]}: {load.origin_city} → {load.destination_city}\n"
                f"Truck #{str(truck.id)[:8]} ETA → {truck.trip_destination}\n\nراجع /admin."
            )
            await _deliver(db, admin_notif, admin.telegram_id, text)


async def notify_auto_connected(db: AsyncSession, load: Load, truck: Truck, match: Match, admin_id: uuid.UUID | None) -> None:
    """
    Sent instead of notify_match_found when load.user_id and
    truck.user_id are already a TrustedPair. Deliberately DOES include
    each party's name and phone -- unlike every other notification in
    this module -- because they already have each other's contact info
    from a prior admin-mediated connection; this just saves them (and
    the admin) from re-doing that handshake for a relationship that's
    already been vetted.
    """
    shipper_notif, created_s = await _create_notification_row(
        db, load.user_id, load.id, NotificationType.AUTO_CONNECTED, truck_id=truck.id, match_id=match.id
    )
    if created_s:
        shipper = await db.get(User, load.user_id)
        carrier = await db.get(User, truck.user_id)
        text = (
            "🤝 تطابق مع ناقل تعاملت معه سابقًا\n\n"
            f"📍 التحميل: {load.origin_city}\n📍 التفريغ: {load.destination_city}\n\n"
            f"🚛 {carrier.name or 'الناقل'} — {carrier.phone or 'لا يوجد رقم مسجل'}\n\n"
            "تم الربط تلقائيًا لأنكما تعاملتما سابقًا عبر المنصة."
        )
        await _deliver(db, shipper_notif, shipper.telegram_id, text)

    carrier_notif, created_c = await _create_notification_row(
        db, truck.user_id, load.id, NotificationType.AUTO_CONNECTED, truck_id=truck.id, match_id=match.id
    )
    if created_c:
        carrier = await db.get(User, truck.user_id)
        shipper = await db.get(User, load.user_id)
        text = (
            "🤝 تطابق مع صاحب حمولة تعاملت معه سابقًا\n\n"
            f"📍 التحميل: {load.origin_city}\n📍 التفريغ: {load.destination_city}\n\n"
            f"📦 {shipper.name or 'صاحب الحمولة'} — {shipper.phone or 'لا يوجد رقم مسجل'}\n\n"
            "تم الربط تلقائيًا لأنكما تعاملتما سابقًا عبر المنصة."
        )
        await _deliver(db, carrier_notif, carrier.telegram_id, text)

    if admin_id:
        admin_notif, created_a = await _create_notification_row(
            db, admin_id, load.id, NotificationType.AUTO_CONNECTED, truck_id=truck.id, match_id=match.id
        )
        if created_a:
            admin = await db.get(User, admin_id)
            text = (
                "🤝 ربط تلقائي (طرفان تم التحقق منهما سابقًا)\n\n"
                f"Load #{str(load.id)[:8]} ↔ Truck #{str(truck.id)[:8]}\n"
                f"{load.origin_city} → {load.destination_city}\n\n"
                "لا حاجة لأي إجراء — تم الربط تلقائيًا لأن هذين المستخدمين تعاملا سابقًا."
            )
            await _deliver(db, admin_notif, admin.telegram_id, text)

    await log_action(
        db, None, "auto_connected_trusted_pair", "match", match.id,
        {"load_id": str(load.id), "truck_id": str(truck.id)},
    )


async def process_new_match(db: AsyncSession, load: Load, truck: Truck, match: Match, admin_id: uuid.UUID | None) -> None:
    """
    Single entry point callers should use right after a NEW match row is
    created (see api/loads.py, api/trucks.py). Decides between the
    normal admin-mediated MATCH_FOUND flow and an immediate
    AUTO_CONNECTED for a known TrustedPair -- callers don't need to know
    which one happens.
    """
    if await is_trusted_pair(db, load.user_id, truck.user_id):
        match.status = MatchStatus.CONNECTED
        load.status = LoadStatus.CONNECTED
        await notify_auto_connected(db, load, truck, match, admin_id)
    else:
        await notify_match_found(db, load, truck, match, admin_id)
