from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

ROLE_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🚛 أنا صاحب شاحنة", callback_data="role:CARRIER")],
    [InlineKeyboardButton(text="📦 أنا صاحب حمولة", callback_data="role:SHIPPER")],
])

YES_NO_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="نعم", callback_data="yn:yes"), InlineKeyboardButton(text="لا", callback_data="yn:no")],
])

CONFIRM_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="✅ تأكيد", callback_data="confirm:yes")],
    [InlineKeyboardButton(text="✏️ تعديل", callback_data="confirm:edit")],
    [InlineKeyboardButton(text="❌ إلغاء", callback_data="confirm:cancel")],
])

MANUAL_TYPE_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🚛 عندي شاحنة", callback_data="manual:truck")],
    [InlineKeyboardButton(text="📦 عندي حمولة", callback_data="manual:load")],
])

# Shown to ADMIN accounts on /start -- gives them one-tap access to the
# admin panel instead of having to remember/type the /admin command.
ADMIN_PANEL_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🛠 لوحة الإدارة", callback_data="adminpanel")],
])


def interest_kb(load_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚛 أنا مهتم", callback_data=f"interest:{load_id}")],
    ])


def admin_match_kb(match_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📞 تواصل", callback_data=f"admin:contact:{match_id}"),
            InlineKeyboardButton(text="✅ تم الربط", callback_data=f"admin:connect:{match_id}"),
        ],
        [
            InlineKeyboardButton(text="❌ رفض", callback_data=f"admin:reject:{match_id}"),
        ],
    ])


REMOVE_KB = ReplyKeyboardRemove()
