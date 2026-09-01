from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.api_client import BackendError, api
from bot.keyboards import ADMIN_PANEL_KB, ROLE_KB
from bot.states import Onboarding

router = Router(name="start")

WELCOME = (
    "مرحبًا بك في منصة ربط الحمولات بالشاحنات 🚛📦\n\n"
    "اختر نوع حسابك:"
)

ADMIN_WELCOME = (
    "مرحبًا بك 👋\n\n"
    "هذا الحساب مسجّل كحساب إدارة (Admin) للمنصة.\n"
    "اضغط الزر أدناه لعرض لوحة التحكم في أي وقت.\n\n"
    "حسابات الإدارة لا تسجّل كصاحب شاحنة أو صاحب حمولة على نفس الحساب — "
    "لتجربة النظام من جهة مستخدم عادي، استخدم حساب تيليجرام آخر."
)

ROLE_LABELS = {"CARRIER": "🚛 صاحب شاحنة", "SHIPPER": "📦 صاحب حمولة"}


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    telegram_id = str(message.from_user.id)
    user = await api.upsert_user(telegram_id, message.from_user.full_name)

    if user["role"] == "ADMIN":
        await message.answer(ADMIN_WELCOME, reply_markup=ADMIN_PANEL_KB)
        return

    if user["role"] in ("CARRIER", "SHIPPER"):
        await message.answer(
            f"أهلاً بعودتك! أنت مسجل حاليًا كـ {ROLE_LABELS[user['role']]}.\n\n"
            "أرسل وصف طلبك الآن بشكل حر، مثال:\n"
            "«شاحنتي في الدمام وأبي حمولة لجدة»\n"
            "أو «عندي 3 تريلات من الدمام لجدة اليوم»"
        )
        await state.set_state(Onboarding.awaiting_request)
        return

    await message.answer(WELCOME, reply_markup=ROLE_KB)


@router.callback_query(F.data.startswith("role:"))
async def choose_role(callback: CallbackQuery, state: FSMContext):
    role = callback.data.split(":", 1)[1]
    telegram_id = str(callback.from_user.id)
    try:
        await api.set_role(telegram_id, role)
    except BackendError as e:
        await callback.message.edit_text(f"تعذر حفظ نوع الحساب: {e.detail}")
        await callback.answer()
        return

    await state.update_data(role=role)
    await callback.message.edit_text(f"تم اختيار: {ROLE_LABELS[role]}\n\nما اسمك؟")
    await state.set_state(Onboarding.name)
    await callback.answer()


@router.message(Onboarding.name)
async def got_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()[:120]
    if not name:
        await message.answer("الرجاء إدخال اسم صحيح.")
        return
    await state.update_data(name=name)
    await api.upsert_user(str(message.from_user.id), name)
    await message.answer("ما رقم جوالك؟ (سيُستخدم فقط من قبل إدارة المنصة للتنسيق ولن يظهر للطرف الآخر)")
    await state.set_state(Onboarding.phone)


@router.message(Onboarding.phone)
async def got_phone(message: Message, state: FSMContext):
    phone = (message.text or "").strip()[:30]
    if not phone:
        await message.answer("الرجاء إدخال رقم جوال صحيح.")
        return
    await state.update_data(phone=phone)
    await api.upsert_user(str(message.from_user.id), None, phone)
    data = await state.get_data()
    role = data.get("role")
    example = (
        "«شاحنتي في الدمام وأبي حمولة لجدة»" if role == "CARRIER"
        else "«عندي 3 تريلات من الدمام لجدة اليوم»"
    )
    await message.answer(f"تمام! الآن أرسل وصف طلبك بشكل حر، مثال:\n{example}")
    await state.set_state(Onboarding.awaiting_request)
