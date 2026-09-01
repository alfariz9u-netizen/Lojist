"""
Admin panel, entirely from Telegram. Every action here calls the
backend's /internal/admin/* endpoints, which re-verify server-side that
the caller's telegram_id resolves to role==ADMIN -- this handler does
NOT itself decide who is an admin (see backend/app/api/admin.py).

The panel is reachable two ways: the /admin command, and a one-tap
"🛠 لوحة الإدارة" button shown to admin accounts on /start (see
handlers/start.py's ADMIN_WELCOME + keyboards.ADMIN_PANEL_KB) -- both
render through the same _send_admin_panel() so they can never drift
out of sync.
"""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.api_client import BackendError, api
from bot.keyboards import admin_match_kb

router = Router(name="admin")


async def _send_admin_panel(target, telegram_id: str) -> None:
    """`target` is anything with an async .answer(text, reply_markup=None)
    method -- a Message (from the /admin command) or a CallbackQuery's
    .message (from the panel button)."""
    try:
        overview = await api.admin_overview(telegram_id)
    except BackendError as e:
        if e.status_code == 403:
            await target.answer("هذا الأمر متاح للإدارة فقط.")
        else:
            await target.answer("تعذر تحميل لوحة الإدارة، حاول لاحقًا.")
        return

    text = (
        "🛠 لوحة الإدارة\n\n"
        f"📦 الحمولات المفتوحة: {overview['open_loads']}\n"
        f"🚛 الشاحنات المتاحة: {overview['available_trucks']}\n"
        f"🔔 التطابقات الجديدة: {overview['new_matches']}\n"
        f"⏳ بانتظار مطابقة: {overview['waiting_for_match']}\n"
    )
    await target.answer(text)

    matches = await api.admin_matches(telegram_id)
    if not matches:
        await target.answer("لا توجد تطابقات جديدة بحاجة لمتابعة حاليًا.")
        return

    for m in matches[:10]:
        load, truck = m["load"], m["truck"]
        text = (
            f"🔔 Match #{m['match_id'][:8]} (score={m['score']})\n"
            f"{load['origin']} → {load['destination']}\n"
            f"Carrier في: {truck['current_city']}"
        )
        await target.answer(text, reply_markup=admin_match_kb(m["match_id"]))


@router.message(Command("admin"))
async def admin_panel(message: Message):
    await _send_admin_panel(message, str(message.from_user.id))


@router.callback_query(F.data == "adminpanel")
async def admin_panel_button(callback: CallbackQuery):
    await _send_admin_panel(callback.message, str(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:"))
async def admin_action(callback: CallbackQuery):
    _, action, match_id = callback.data.split(":", 2)
    telegram_id = str(callback.from_user.id)
    try:
        result = await api.admin_action(telegram_id, action, match_id=match_id)
    except BackendError as e:
        await callback.answer(e.detail, show_alert=True)
        return

    if action == "contact":
        shipper, carrier = result["shipper"], result["carrier"]
        text = (
            "📞 بيانات التواصل\n\n"
            f"صاحب الحمولة: {shipper.get('name') or '-'} — {shipper.get('phone') or 'لا يوجد رقم'}\n"
            f"الناقل: {carrier.get('name') or '-'} — {carrier.get('phone') or 'لا يوجد رقم'}"
        )
        await callback.message.answer(text)
    elif action == "connect":
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("✅ تم تحديث الحالة إلى: تم الربط.")
    elif action == "reject":
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("❌ تم رفض هذا التطابق.")
    await callback.answer()
