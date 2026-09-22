"""Diyor Group — Telegram GPS Attendance Bot (Aiogram 3).

Ходимларнинг телефон рақамини тасдиқлаш, GPS орқали ишга келиш ва кетишини
назорат қилиш ва МойСклад / Dashboard билан синхронизация қилиш боти.
"""
import re
import math
import os
import sys
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

# Fix Windows console encoding for UTF-8 emojis
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure backend directory is accessible for models and settings
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from aiogram import Bot, Dispatcher, F, types, Router
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from config import settings
from core.database import AsyncSessionLocal, engine, Base
from models.hr import WorkTimesheet, AuthorizedEmployee, Workplace
from services.moysklad_client import MoySkladClient

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("diyor_bot")

UZ_TZ = timezone(timedelta(hours=5))


# ═══════════════ YORDAMCHI FUNKSIYALAR ═══════════════

def normalize_phone_digits(raw: str) -> str:
    """Telefon raqamidan faqat raqamlarni ajratib olish (oxirgi 9 ta raqam)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) >= 9:
        return digits[-9:]
    return digits


def calculate_haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Ikki nuqta orasidagi masofani hisoblash (metrlarda)."""
    R = 6371000.0  # Yer radiusi metrlarda
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


# ═══════════════ KLAVIATURALAR ═══════════════

def get_auth_keyboard() -> ReplyKeyboardMarkup:
    """Telefon raqamini yuborish tugmasi."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Телефон рақамимни юбориш", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="«Телефон рақамимни юбориш» тугмасини босинг..."
    )


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Тасдиқланган ходим учун доимий асосий меню."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🟢 Ишга келдим (GPS)"),
                KeyboardButton(text="🔴 Ишдан кетдим (GPS)")
            ]
        ],
        resize_keyboard=True,
        input_field_placeholder="Иш ҳолатини танланг..."
    )


def get_location_keyboard() -> ReplyKeyboardMarkup:
    """GPS геолокацияни сўраш клавиатураси."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📍 Геолокацияни юбориш", request_location=True)
            ],
            [
                KeyboardButton(text="❌ Бекор қилиш")
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="«Геолокацияни юбориш» тугмасини босинг..."
    )


# ═══════════════ FSM STATES ═══════════════

class AttendanceStates(StatesGroup):
    waiting_for_location = State()


# ═══════════════ BAZA VA MOYSKLAD QIDIRUVLARI ═══════════════

async def get_authorized_employee(tg_id: int) -> Optional[AuthorizedEmployee]:
    """Telegram ID bo'yicha tasdiqlangan faol xodimni olish."""
    async with AsyncSessionLocal() as session:
        stmt = select(AuthorizedEmployee).where(
            AuthorizedEmployee.telegram_id == tg_id,
            AuthorizedEmployee.is_active == 1
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()


async def find_employee_by_phone(raw_phone: str) -> Optional[Dict[str, Any]]:
    """
    Telefon raqami (oxirgi 9 ta raqami) bo'yicha MoySklad va mahalliy bazadan qidirish.
    """
    p9 = normalize_phone_digits(raw_phone)
    if len(p9) < 9:
        return None

    # 1. Mahalliy authorized_employees jadvalidan tekshirish
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(AuthorizedEmployee).where(AuthorizedEmployee.is_active == 1)
            res = await session.execute(stmt)
            for emp in res.scalars().all():
                if normalize_phone_digits(emp.phone_number) == p9:
                    return {
                        "id": emp.id,
                        "name": emp.employee_name,
                        "phone": emp.phone_number,
                        "moysklad_id": emp.moysklad_id,
                        "source": "local_db"
                    }
    except Exception as e:
        logger.warning(f"Mahalliy DB qidiruvida xato: {e}")

    # 2. MoySklad API orqali qidirish (/entity/employee)
    try:
        ms = MoySkladClient()
        resp = await ms._request("GET", "/entity/employee", params={"limit": 100})
        rows = resp.get("rows", [])
        for r in rows:
            if not r.get("archived", False):
                ms_phone = r.get("phone")
                if ms_phone and normalize_phone_digits(ms_phone) == p9:
                    return {
                        "name": r.get("name", "Ходим"),
                        "phone": ms_phone,
                        "moysklad_id": r.get("id"),
                        "source": "moysklad"
                    }
    except Exception as e:
        logger.warning(f"MoySklad xodimlarni qidirishda xatolik: {e}")

    return None


async def save_authorized_employee(tg_user: types.User, match: Dict[str, Any], full_phone: str):
    """Xodimni Telegram ID'si bilan bazaga bog'lash."""
    emp_name = match.get("name", "Ходим")
    moysklad_id = match.get("moysklad_id")

    digits = re.sub(r"\D", "", full_phone)
    formatted_phone = f"+{digits}" if not digits.startswith("+") else digits

    async with AsyncSessionLocal() as session:
        stmt = select(AuthorizedEmployee).where(
            (AuthorizedEmployee.telegram_id == tg_user.id) |
            (AuthorizedEmployee.phone_number == formatted_phone)
        )
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()

        if record:
            record.employee_name = emp_name
            record.phone_number = formatted_phone
            record.telegram_id = tg_user.id
            record.telegram_username = tg_user.username
            record.moysklad_id = moysklad_id
            record.is_active = 1
            record.authorized_at = datetime.utcnow()
        else:
            record = AuthorizedEmployee(
                employee_name=emp_name,
                phone_number=formatted_phone,
                telegram_id=tg_user.id,
                telegram_username=tg_user.username,
                moysklad_id=moysklad_id,
                role="Ходим",
                is_active=1,
                authorized_at=datetime.utcnow()
            )
            session.add(record)

        await session.commit()


# ═══════════════ ROUTER VA HANDLERLAR ═══════════════

router = Router()


@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """
    /start buyrug'i:
    - Foydalanuvchi tasdiqlangan bo'lsa: Asosiy 2 ta GPS menyusini ko'rsatadi.
    - Tasdiqlanmagan bo'lsa: «📱 Телефон рақамимни юбориш» tugmasini chiqaradi.
    """
    await state.clear()

    # Agar referral kodi bilan kirgan bo'lsa (/start REF-xxx)
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("REF-"):
        ref_code = args[1].strip()
        ref_welcome = (
            f"🤝 <b>Diyor Group Ҳамкорлик Дастурига хуш келибсиз!</b>\n\n"
            f"Сизнинг рефераль кодингиз: <code>{ref_code}</code>\n"
            f"Diyor Group дўконларидан сантехника маҳсулотларини харид қилинг "
            f"ёки мижозларни жалб қилиб, ҳар бир хариддан <b>5% бонусни</b> нақд ёки карта орқали олинг!\n\n"
            f"📞 Маълумот учун: +998 90 710 25 55"
        )
        await message.answer(ref_welcome, parse_mode="HTML")
        return

    # Xodim avtorizatsiyasini tekshirish
    emp = await get_authorized_employee(message.from_user.id)

    if emp:
        welcome_text = (
            f"👋 <b>Ассалому алайкум, {emp.employee_name}!</b>\n\n"
            f"🏢 <b>Diyor Group</b> — Сиз тизимда тасдиқлангансиз.\n\n"
            f"Ишга келиш ва кетишингизни белгилаш учун қуйидаги тугмалардан фойдаланинг:"
        )
        await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    else:
        auth_prompt = (
            f"Ассалому алайкум! Diyor Group тизимига хуш келибсиз.\n\n"
            f"Шахсингизни тасдиқлаш ва иш жойингизни белгилаш учун телефон рақамингизни юборинг."
        )
        await message.answer(auth_prompt, reply_markup=get_auth_keyboard(), parse_mode="HTML")


@router.message(F.contact)
async def handle_contact(message: types.Message):
    """
    «📱 Телефон рақамимни юбориш» тугмаси орқали контакт келганда.
    """
    contact = message.contact

    # Xavfsizlik tekshiruvi: boshqa birovning kontaktini yubormasligi kerak
    if contact.user_id and contact.user_id != message.from_user.id:
        await message.answer(
            "❌ <b>Хатолик:</b> Илтимос, фақат ўзингизнинг телефон рақамингизни юборинг!",
            reply_markup=get_auth_keyboard(),
            parse_mode="HTML"
        )
        return

    raw_phone = contact.phone_number
    match = await find_employee_by_phone(raw_phone)

    if match:
        emp_name = match.get("name", "Ходим")
        await save_authorized_employee(message.from_user, match, raw_phone)
        logger.info(f"Xodim muvaffaqiyatli tasdiqlandi: {emp_name} ({raw_phone})")

        success_text = f"Тасдиқланди: {emp_name}! Энди ишга келиш ва кетишингизни белгилашингиз мумкин"
        await message.answer(success_text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    else:
        logger.warning(f"Begona raqam urinishi: {raw_phone} ({message.from_user.full_name})")
        reject_text = "Сиз Diyor Group ходимлари рўйхатида топилмадингиз. Илтимос, раҳбариятга мурожаат қилинг"
        await message.answer(reject_text, reply_markup=get_auth_keyboard(), parse_mode="HTML")


@router.message(F.text.regexp(r"^\+?998\d{9}$") | F.text.regexp(r"^\d{9}$"))
async def handle_text_phone(message: types.Message):
    """
    Foydalanuvchi telefon raqamini matn ko'rinishida yozib yuborganda ham qabul qilish.
    """
    match = await find_employee_by_phone(message.text)
    if match:
        emp_name = match.get("name", "Ходим")
        await save_authorized_employee(message.from_user, match, message.text)
        success_text = f"Тасдиқланди: {emp_name}! Энди ишга келиш ва кетишингизни белгилашингиз мумкин"
        await message.answer(success_text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    else:
        reject_text = "Сиз Diyor Group ходимлари рўйхатида топилмадингиз. Илтимос, раҳбариятга мурожаат қилинг"
        await message.answer(reject_text, reply_markup=get_auth_keyboard(), parse_mode="HTML")


@router.message(F.text == "🟢 Ишга келдим (GPS)")
async def btn_checkin(message: types.Message, state: FSMContext):
    """Ишга келиш GPS сўрови."""
    emp = await get_authorized_employee(message.from_user.id)
    if not emp:
        await message.answer(
            "Ассалому алайкум! Diyor Group тизимига хуш келибсиз.\n"
            "Шахсингизни тасдиқлаш ва иш жойингизни белгилаш учун телефон рақамингизни юборинг.",
            reply_markup=get_auth_keyboard(),
            parse_mode="HTML"
        )
        return

    await state.set_state(AttendanceStates.waiting_for_location)
    await state.update_data(action="CHECKIN", employee_id=emp.id, employee_name=emp.employee_name)

    prompt = (
        f"📍 <b>Ишга келишни қайд этиш</b>\n\n"
        f"Ҳурматли <b>{emp.employee_name}</b>, телефонингизда GPS (геолокация) ёқилганлигига ишонч ҳосил қилинг ва "
        f"пастдаги <b>«📍 Геолокацияни юбориш»</b> тугмасини босинг."
    )
    await message.answer(prompt, reply_markup=get_location_keyboard(), parse_mode="HTML")


@router.message(F.text == "🔴 Ишдан кетдим (GPS)")
async def btn_checkout(message: types.Message, state: FSMContext):
    """Ишдан кетиш GPS сўрови."""
    emp = await get_authorized_employee(message.from_user.id)
    if not emp:
        await message.answer(
            "Ассалому алайкум! Diyor Group тизимига хуш келибсиз.\n"
            "Шахсингизни тасдиқлаш ва иш жойингизни белгилаш учун телефон рақамингизни юборинг.",
            reply_markup=get_auth_keyboard(),
            parse_mode="HTML"
        )
        return

    await state.set_state(AttendanceStates.waiting_for_location)
    await state.update_data(action="CHECKOUT", employee_id=emp.id, employee_name=emp.employee_name)

    prompt = (
        f"🏁 <b>Иш сменасини якунлаш</b>\n\n"
        f"Ҳурматли <b>{emp.employee_name}</b>, бугунги иш вақтингизни ҳисоблаш учун "
        f"пастдаги <b>«📍 Геолокацияни юбориш»</b> тугмасини босинг."
    )
    await message.answer(prompt, reply_markup=get_location_keyboard(), parse_mode="HTML")


@router.message(F.text == "❌ Бекор қилиш")
async def btn_cancel(message: types.Message, state: FSMContext):
    """Амални бекор қилиш."""
    await state.clear()
    emp = await get_authorized_employee(message.from_user.id)
    kb = get_main_keyboard() if emp else get_auth_keyboard()
    await message.answer("❌ Амал бекор қилинди.", reply_markup=kb)


@router.message(F.location)
async def handle_location(message: types.Message, state: FSMContext):
    """
    Ходим томонидан жонли GPS геолокация юборилганда:
    1. Иш жойи (дўкон ёки объект) масофасини солиштиради.
    2. Базадаги work_timesheets жадвалига ёзади (Dashboard'да дарҳол кўринади).
    3. Ходимга тасдиқ хабарини қайтаради.
    """
    emp = await get_authorized_employee(message.from_user.id)
    if not emp:
        await state.clear()
        await message.answer(
            "Шахсингизни тасдиқлаш учун телефон рақамингизни юборинг.",
            reply_markup=get_auth_keyboard()
        )
        return

    data = await state.get_data()
    action = data.get("action", "CHECKIN")
    await state.clear()

    user_lat = message.location.latitude
    user_lon = message.location.longitude
    now_local = datetime.now(UZ_TZ)
    now_utc = datetime.utcnow()

    # Иш объектларини текшириш (Марказий дўкон ёки базадаги объектлар)
    target_name = "Марказий дўкон (Бухоро)"
    target_lat = getattr(settings, "STORE_LAT", 39.7747)
    target_lon = getattr(settings, "STORE_LON", 64.4286)
    allowed_radius = getattr(settings, "MAX_DISTANCE_METERS", 150.0)

    min_dist = calculate_haversine_distance(user_lat, user_lon, target_lat, target_lon)

    # Базадаги қўшимча иш жойлари бўлса улар билан ҳам солиштириш
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(Workplace).where(Workplace.is_active == 1)
            res = await session.execute(stmt)
            workplaces = res.scalars().all()
            for wp in workplaces:
                d = calculate_haversine_distance(user_lat, user_lon, wp.latitude, wp.longitude)
                if d < min_dist:
                    min_dist = d
                    target_name = wp.name
                    allowed_radius = wp.radius_meters
    except Exception as e:
        logger.warning(f"Workplaces tekshirishda xato: {e}")

    within_geofence = min_dist <= (allowed_radius + 50)  # GPS ноаниқлиги учун +50м заҳира
    status_label = "Ўз вақтида (GPS тасдиқланди)" if within_geofence else f"Объектдан ташқарида ({int(min_dist)}м)"

    # Кечикиш текшируви
    start_hour = getattr(settings, "store_work_start_hour", 9)
    if now_local.hour > start_hour or (now_local.hour == start_hour and now_local.minute > 15):
        if within_geofence:
            late_min = (now_local.hour - start_hour) * 60 + now_local.minute
            status_label = f"Кечикди ({late_min} дақиқа)"

    # Базага сақлаш
    try:
        async with AsyncSessionLocal() as session:
            today_start = datetime(now_utc.year, now_utc.month, now_utc.day)

            if action == "CHECKIN":
                ts = WorkTimesheet(
                    employee_id=emp.id,
                    employee_name=emp.employee_name,
                    object_name=target_name,
                    checkin_time=now_utc,
                    status="CHECKED_IN",
                    latitude=user_lat,
                    longitude=user_lon,
                    distance_meters=round(min_dist, 1),
                    attendance_status=status_label
                )
                session.add(ts)
                await session.commit()

                resp_text = (
                    f"🟢 <b>Ишга келиш муваффақиятли қайд этилди!</b>\n\n"
                    f"👤 <b>Ходим:</b> {emp.employee_name}\n"
                    f"🏢 <b>Объект:</b> {target_name}\n"
                    f"⏰ <b>Вақт:</b> {now_local.strftime('%H:%M:%S')} ({now_local.strftime('%d.%m.%Y')})\n"
                    f"📏 <b>Масофа:</b> {int(min_dist)} метр\n"
                    f"📊 <b>Ҳолат:</b> {status_label}\n\n"
                    f"<i>Давомад дашбордда автоматик янгиланди. Яхши иш куни тилаймиз!</i>"
                )
            else:
                # CHECKOUT
                stmt = select(WorkTimesheet).where(
                    WorkTimesheet.employee_id == emp.id,
                    WorkTimesheet.checkin_time >= today_start,
                    WorkTimesheet.status == "CHECKED_IN"
                ).order_by(WorkTimesheet.checkin_time.desc())
                res = await session.execute(stmt)
                active_ts = res.scalars().first()

                hours = 0.0
                if active_ts:
                    active_ts.checkout_time = now_utc
                    active_ts.status = "CHECKED_OUT"
                    delta = (now_utc - active_ts.checkin_time).total_seconds() / 3600.0
                    hours = round(max(0.1, delta), 2)
                    active_ts.total_hours = hours
                    await session.commit()
                else:
                    ts = WorkTimesheet(
                        employee_id=emp.id,
                        employee_name=emp.employee_name,
                        object_name=target_name,
                        checkin_time=now_utc,
                        checkout_time=now_utc,
                        total_hours=0.0,
                        status="CHECKED_OUT",
                        latitude=user_lat,
                        longitude=user_lon,
                        distance_meters=round(min_dist, 1),
                        attendance_status="Иш якунланди"
                    )
                    session.add(ts)
                    await session.commit()

                resp_text = (
                    f"🔴 <b>Иш сменаси муваффақиятли якунланди!</b>\n\n"
                    f"👤 <b>Ходим:</b> {emp.employee_name}\n"
                    f"⏰ <b>Кетиш вақти:</b> {now_local.strftime('%H:%M:%S')}\n"
                    f"⏱ <b>Ишланган вақт:</b> {hours} соат\n\n"
                    f"<i>Давомад дашбордда акс эттирилди. Ҳорманг!</i>"
                )

        await message.answer(resp_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Давомадни сақлашда хатолик: {e}")
        await message.answer(
            f"⚠️ Маълумотни сақлашда техник хатолик юз берди: {e}",
            reply_markup=get_main_keyboard()
        )


# ═══════════════ ASOSIY ISHGA TUSHIRISH FUNKSIYASI ═══════════════

async def main():
    token = settings.telegram_bot_token
    if not token or token.startswith("test_"):
        print("❌ TELEGRAM_BOT_TOKEN топилмади ёки тест ҳолатида!")
        return

    # Ma'lumotlar bazasi jadvallarini tekshirish
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    me = await bot.get_me()
    print("=" * 60)
    print(f"🤖 Diyor Group Telegram Bot ишга тушди: @{me.username} ({me.full_name})")
    print(f"📡 Шахсий чатларда /start ва телефон тасдиғини кутмоқда...")
    print(f"💼 Давомад базаси: diyorgroup.db -> work_timesheets")
    print("=" * 60)

    try:
        # Eski to'планган хабарларни тозалаб янгиларини олиш
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    except Exception as e:
        print(f"❌ Ботда хатолик: {e}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n🛑 Бот тўхтатилди.")
