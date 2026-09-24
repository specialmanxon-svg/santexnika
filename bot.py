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
from aiogram.exceptions import TelegramConflictError
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from config import settings
from core.database import AsyncSessionLocal, engine, Base
from models.hr import WorkTimesheet, AuthorizedEmployee, Workplace
from models.task import Task
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
    """
    Ikki nuqta orasidagi masofani hisoblash (metrlarda).
    Avtomatik ravishda Latitude va Longitude almashib qolgan bo'lsa to'g'rilaydi.
    """
    # Auto-detect and swap if lat and lon were inverted (Central Asia: Lat 37-45, Lon 56-73)
    if lat1 > 50.0 and lon1 < 50.0:
        lat1, lon1 = lon1, lat1
    if lat2 > 50.0 and lon2 < 50.0:
        lat2, lon2 = lon2, lat2

    R = 6371000.0  # Yer radiusi metrlarda
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c



async def sync_to_backend_api(payload: dict):
    """Ходимнинг ботдан юборган давомадини тўғридан-тўғри Backend API га узатиш."""
    targets = []
    custom = os.getenv("BACKEND_API_URL")
    if custom and custom.strip():
        targets.append(custom.strip().rstrip("/"))
    targets.extend([
        "https://santexnika.onrender.com/api/v1",
        "http://127.0.0.1:8000/api/v1"
    ])
    try:
        import httpx
        for base in targets:
            endpoint = f"{base}/hr/check-in" if payload.get("action") == "check_in" else f"{base}/hr/check-out"
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    res = await client.post(endpoint, json=payload)
                    if res.status_code in (200, 201):
                        logger.info(f"✅ Backend API ({base}) га давомад узатилди")
                        break
            except Exception as ex:
                logger.debug(f"Backend API ({base}) уланиш синови: {ex}")
    except Exception as e:
        logger.warning(f"sync_to_backend_api хатоси: {e}")


async def send_attendance_notification(bot: Bot, text: str):
    """
    2. Умумий давомад назорати (Фақат Раҳбариятга / Каналга / Гуруҳга):
    - «Янги давомад қайди (GPS)» ва «Иш сменаси якунланди (GPS)» каби мониторинг хабарлари
      фақат махсус Раҳбарият гуруҳига (MANAGEMENT_GROUP_ID) ёки Админнинг аниқ ADMIN_CHAT_IDсига юборилади.
    - Оддий ходимларнинг шахсий чатига БОШҚА ходимлар ҳақида ҳеч қачон хабар ЮБОРИЛМАЙДИ!
    - Барча фойдаланувчилар бўйича цикл (for user in all_users) йўқ, фақат раҳбарият манзили олинади.
    """
    management_group = (
        os.getenv("MANAGEMENT_GROUP_ID")
        or getattr(settings, "management_group_id", None)
        or os.getenv("TELEGRAM_GROUP_ID")
        or getattr(settings, "telegram_group_id", None)
    )
    admin_chat_id = (
        os.getenv("ADMIN_CHAT_ID")
        or getattr(settings, "admin_chat_id", None)
        or getattr(settings, "ADMIN_CHAT_ID", None)
        or getattr(settings, "telegram_ceo_chat_id", None)
        or "5950380558"
    )

    targets = set()
    # Агар гуруҳ ID кўрсатилган бўлса, фақат гуруҳга юборилади
    if management_group and str(management_group).strip():
        targets.add(str(management_group).strip())
    # Агар махсус гуруҳ бўлмаса, фақат расмий раҳбар (Админ) чатига юборилади
    elif admin_chat_id and str(admin_chat_id).strip():
        targets.add(str(admin_chat_id).strip())

    for target_chat in targets:
        try:
            await bot.send_message(chat_id=target_chat, text=text, parse_mode="HTML")
            logger.info(f"Давомад хабари раҳбариятга юборилди: {target_chat}")
        except Exception as ex:
            logger.warning(f"Давомад хабарини раҳбариятга юборишда хатолик ({target_chat}): {ex}")


# Қўшимча мувофиқлик учун
notify_management = send_attendance_notification


# ═══════════════ KLAVIATURALAR ═══════════════

def get_auth_keyboard() -> ReplyKeyboardMarkup:
    """Telefon raqamini yuborish tugmasi."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Телефон рақамни юбориш", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="«Телефон рақамни юбориш» тугмасини босинг..."
    )


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Тасдиқланган ходим учун доимий асосий 4 талик меню."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📍 Ишга келиш"),
                KeyboardButton(text="🏁 Ишдан кетиш")
            ],
            [
                KeyboardButton(text="📋 Менинг топшириқларим"),
                KeyboardButton(text="➕ Янги топшириқ")
            ]
        ],
        resize_keyboard=True
    )


def get_location_keyboard() -> ReplyKeyboardMarkup:
    """GPS локацияни сўраш клавиатураси."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Ҳозирги локациямни юбориш", request_location=True)],
            [KeyboardButton(text="❌ Бекор қилиш")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Локация юбориш тугмасини босинг..."
    )


# ═══════════════ FSM STATES ═══════════════

class AttendanceStates(StatesGroup):
    waiting_for_location = State()


class TaskStates(StatesGroup):
    waiting_for_response = State()

# ═══════════════ BAZA VA MOYSKLAD QIDIRUVLARI ═══════════════

async def get_authorized_employee(tg_id: int, username: Optional[str] = None) -> Optional[AuthorizedEmployee]:
    """Telegram ID yoki username bo'yicha tasdiqlangan faol xodimni olish."""
    async with AsyncSessionLocal() as session:
        stmt = select(AuthorizedEmployee).where(
            AuthorizedEmployee.telegram_id == tg_id,
            AuthorizedEmployee.is_active == 1
        )
        res = await session.execute(stmt)
        emp = res.scalar_one_or_none()
        if emp:
            if username:
                clean_u = username.lstrip("@").strip()
                if clean_u and emp.telegram_username != clean_u:
                    emp.telegram_username = clean_u
                    await session.commit()
            return emp

        if not emp and username:
            clean_u = username.lstrip("@").strip()
            if clean_u:
                stmt_u = select(AuthorizedEmployee).where(
                    AuthorizedEmployee.telegram_username.ilike(clean_u),
                    AuthorizedEmployee.is_active == 1
                )
                res_u = await session.execute(stmt_u)
                emp = res_u.scalar_one_or_none()
                if emp:
                    emp.telegram_id = tg_id
                    emp.telegram_username = clean_u
                    await session.commit()
                    logger.info(f"Ходим {emp.employee_name} Telegram ID {tg_id} билан автоматик боғланди (username: @{clean_u})")
        return emp


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
    - Foydalanuvchi tasdiqlangan bo'lsa: Asosiy 4 ta menyuni ko'rsatadi:
      [ 📍 Ишга келиш ] [ 🏁 Ишдан кетиш ]
      [ 📋 Менинг топшириқларим ] [ ➕ Янги топшириқ ]
    - Tasdiqlanmagan bo'lsa: «📱 Телефон рақамни юбориш» tugmasini chiqaradi.
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
    emp = await get_authorized_employee(message.from_user.id, message.from_user.username)

    if emp:
        welcome_text = (
            f"👋 <b>Ассалому алайкум, {emp.employee_name}!</b>\n\n"
            f"🏢 <b>Diyor Group</b> — Сиз тизимда тасдиқлангансиз.\n\n"
            f"Қуйидаги меню орқали ишга келиш/кетишни белгилашингиз ва топшириқлар билан ишлашингиз мумкин:"
        )
        await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    else:
        auth_prompt = (
            f"👋 <b>Ассалому алайкум! Diyor Group тизимига хуш келибсиз.</b>\n\n"
            f"Шахсингизни тасдиқлаш ва тизимдан фойдаланиш учун илтимос, "
            f"пастдаги «📱 Телефон рақамни юбориш» тугмасини босинг."
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


@router.message(F.text.contains("Ишга келиш") | F.text.contains("Ишга келдим"))
async def btn_checkin(message: types.Message, state: FSMContext):
    """Ишга келиш GPS сўрови."""
    emp = await get_authorized_employee(message.from_user.id, message.from_user.username)
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
        f"📍 Ҳурматли <b>{emp.employee_name}</b>!\n\n"
        f"Ишга келишни қайд этиш учун, илтимос, пастдаги «📍 Ҳозирги локациямни юбориш» тугмасини босинг:"
    )
    await message.answer(prompt, reply_markup=get_location_keyboard(), parse_mode="HTML")


@router.message(F.text.contains("Ишдан кетиш") | F.text.contains("Ишдан кетдим"))
async def btn_checkout(message: types.Message, state: FSMContext):
    """Ишдан кетиш GPS сўрови."""
    emp = await get_authorized_employee(message.from_user.id, message.from_user.username)
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
        f"🏁 Ҳурматли <b>{emp.employee_name}</b>!\n\n"
        f"Ишдан кетишни қайд этиш учун, илтимос, пастдаги «📍 Ҳозирги локациямни юбориш» тугмасини босинг:"
    )
    await message.answer(prompt, reply_markup=get_location_keyboard(), parse_mode="HTML")


@router.message(F.text.contains("Бекор қилиш") | (F.text == "❌ Бекор қилиш"))
async def btn_cancel(message: types.Message, state: FSMContext):
    """Амални бекор қилиш."""
    await state.clear()
    emp = await get_authorized_employee(message.from_user.id, message.from_user.username)
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
    # Auto-detect and swap if user coordinates were inverted (Central Asia: Lat 37-45, Lon 56-73)
    if user_lat > 50.0 and user_lon < 50.0:
        user_lat, user_lon = user_lon, user_lat

    now_local = datetime.now(UZ_TZ)
    now_utc = datetime.utcnow()

    matched_wp = None
    closest_wp = None
    min_dist = float('inf')

    # Базадаги барча фаол иш объектлари билан солиштириш
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(Workplace).where(Workplace.is_active == 1)
            res = await session.execute(stmt)
            workplaces = list(res.scalars().all())

            if not workplaces:
                default_lat = getattr(settings, "STORE_LAT", 39.748992)
                default_lon = getattr(settings, "STORE_LON", 64.432118)
                default_radius = getattr(settings, "MAX_DISTANCE_METERS", 150.0)
                workplaces = [
                    Workplace(
                        id=1,
                        name="Марказий дўкон (Бухоро)",
                        latitude=default_lat,
                        longitude=default_lon,
                        radius_meters=default_radius,
                        is_active=1
                    )
                ]

            for wp in workplaces:
                d = calculate_haversine_distance(user_lat, user_lon, wp.latitude, wp.longitude)
                if d < min_dist:
                    min_dist = d
                    closest_wp = wp
                if d <= wp.radius_meters:
                    matched_wp = wp
                    min_dist = d
                    break
    except Exception as e:
        logger.warning(f"Workplaces tekshirishda xato: {e}")

    if matched_wp:
        target_name = matched_wp.name
        allowed_radius = matched_wp.radius_meters
        within_geofence = True
    elif closest_wp:
        target_name = closest_wp.name
        allowed_radius = closest_wp.radius_meters
        within_geofence = False
    else:
        target_name = "Марказий дўкон (Бухоро)"
        allowed_radius = 150.0
        within_geofence = False

    # Агар объектдан ташқарида бўлса — дарҳол рад этилади ва базага ёзилмайди!
    if not within_geofence:
        if action == "CHECKIN":
            fail_msg = (
                f"❌ <b>Сиз иш жойида эмассиз.</b>\n\n"
                f"🏢 <b>Объект:</b> {target_name}\n"
                f"📏 <b>Масофа:</b> {int(min_dist)} метр\n"
                f"⭕️ <b>Рухсат этилган радиус:</b> {int(allowed_radius)} метр\n\n"
                f"<i>Илтимос, дўкон ёки объект ҳудудига яқин келиб қайта уриниб кўринг!</i>"
            )
        else:
            fail_msg = (
                f"❌ <b>Сиз иш жойида эмассиз.</b>\n\n"
                f"🏢 <b>Объект:</b> {target_name}\n"
                f"📏 <b>Масофа:</b> {int(min_dist)} метр\n"
                f"⭕️ <b>Рухсат этилган радиус:</b> {int(allowed_radius)} метр\n\n"
                f"<i>Илтимос, ишдан кетишни қайд этиш учун объект ҳудудига яқин келиб қайта уриниб кўринг!</i>"
            )
        await message.answer(fail_msg, reply_markup=get_main_keyboard(), parse_mode="HTML")
        return

    # Кечикиш текшируви
    status_label = "Ўз вақтида (GPS тасдиқланди)"
    start_hour = getattr(settings, "store_work_start_hour", 9)
    if now_local.hour > start_hour or (now_local.hour == start_hour and now_local.minute > 15):
        late_min = (now_local.hour - start_hour) * 60 + now_local.minute
        status_label = f"Кечикди ({late_min} дақиқа)"

    # Базага ва Backend API га сақлаш (Масофа 150м ичида бўлганда)
    try:
        # 1. Backend API (Render / Local) га тўғридан-тўғри синхронизация
        payload = {
            "employee_id": emp.id,
            "employee_name": emp.employee_name,
            "action": "check_in" if action == "CHECKIN" else "check_out",
            "latitude": user_lat,
            "longitude": user_lon,
            "source": "Telegram",
            "device_info": "📱 Telegram",
            "object_name": target_name,
            "distance_meters": round(min_dist, 1),
            "timestamp": datetime.now().isoformat()
        }
        asyncio.create_task(sync_to_backend_api(payload))

        # 2. Локал базага тўғридан-тўғри ёзиш
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
                    attendance_status=status_label,
                    device_info="📱 Telegram"
                )
                session.add(ts)
                await session.commit()

                resp_text = (
                    f"✅ <b>Ишга келишингиз қайд этилди. Объект: {target_name}</b>\n\n"
                    f"👤 <b>Ходим:</b> {emp.employee_name}\n"
                    f"⏰ <b>Вақт:</b> {now_local.strftime('%H:%M:%S')} ({now_local.strftime('%d.%m.%Y')})\n"
                    f"📏 <b>Масофа:</b> {int(min_dist)} метр\n"
                    f"📊 <b>Ҳолат:</b> {status_label}\n\n"
                    f"<i>Давомад бошқарув панели (Dashboard) га узатилди. Яхши иш куни тилаймиз!</i>"
                )

                # 3. Раҳбарият гуруҳига билдиришнома юбориш (Фақат Раҳбариятга)
                mgmt_text = (
                    f"📍 <b>Янги давомад қайди (GPS)</b>\n\n"
                    f"👤 <b>Ходим:</b> {emp.employee_name}\n"
                    f"🏢 <b>Объект:</b> {target_name}\n"
                    f"⏰ <b>Вақти:</b> {now_local.strftime('%H:%M:%S')} ({now_local.strftime('%d.%m.%Y')})\n"
                    f"📏 <b>Масофа:</b> {int(min_dist)} метр (Радиус: {int(allowed_radius)}м)\n"
                    f"📊 <b>Ҳолат:</b> {status_label}\n"
                    f"📱 <b>Манба:</b> 📱 Telegram\n\n"
                    f"🌐 <a href='https://diyorgroup.uz/index.html#hr'>Дашбордда кўриш</a>"
                )
                asyncio.create_task(send_attendance_notification(message.bot, mgmt_text))

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
                    active_ts.device_info = "📱 Telegram"
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
                        attendance_status="Иш якунланди",
                        device_info="📱 Telegram"
                    )
                    session.add(ts)
                    await session.commit()

                resp_text = (
                    f"✅ <b>Иш вақтингиз якунланди! Кунингиз хайрли ўтсин.</b>\n\n"
                    f"👤 <b>Ходим:</b> {emp.employee_name}\n"
                    f"🏢 <b>Объект:</b> <b>{target_name}</b>\n"
                    f"⏰ <b>Чиқиш вақти:</b> {now_local.strftime('%H:%M:%S')} ({now_local.strftime('%d.%m.%Y')})\n"
                    f"⏱ <b>Ишланган вақт:</b> {hours} соат\n\n"
                    f"<i>Чиқиш вақти дашбордда муваффақиятли қайд этилди. Ҳорманг!</i>"
                )

                # 3. Раҳбарият гуруҳига билдиришнома юбориш (Фақат Раҳбариятга)
                mgmt_text = (
                    f"🏁 <b>Иш сменаси якунланди (GPS)</b>\n\n"
                    f"👤 <b>Ходим:</b> {emp.employee_name}\n"
                    f"🏢 <b>Объект:</b> {target_name}\n"
                    f"⏰ <b>Чиқиш вақти:</b> {now_local.strftime('%H:%M:%S')} ({now_local.strftime('%d.%m.%Y')})\n"
                    f"⏱ <b>Ишланган вақт:</b> {hours} соат\n"
                    f"📱 <b>Манба:</b> 📱 Telegram"
                )
                asyncio.create_task(send_attendance_notification(message.bot, mgmt_text))

        # 1. Шахсий хабарнома — фақат ушбу ходимнинг шахсий чатига
        await message.answer(resp_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Давомадни сақлашда хатолик: {e}")
        await message.answer(
            f"⚠️ Маълумотни сақлашда техник хатолик юз берди: {e}",
            reply_markup=get_main_keyboard()
        )


# ═══════════════ ТОПШИРИҚЛАР БЎЛИМИ (TASK MANAGER) ═══════════════

def format_task_deadline(dl: Optional[datetime]) -> str:
    """Топшириқ муддатини чиройли форматда чиқариш."""
    if not dl:
        return "—"
    if dl.tzinfo is None:
        dl = dl.replace(tzinfo=timezone.utc).astimezone(UZ_TZ)
    return dl.strftime("%d.%m.%Y %H:%M")


async def get_employee_tasks(tg_id: int):
    """Ходимнинг фаол ва бажарилган топшириқларини базадан олиш."""
    async with AsyncSessionLocal() as session:
        # Фаол топшириқлар (new, in_progress)
        stmt_act = select(Task).where(
            Task.assigned_telegram_id == tg_id,
            Task.status.in_(["new", "in_progress"])
        ).order_by(Task.created_at.desc())
        res_act = await session.execute(stmt_act)
        active_tasks = list(res_act.scalars().all())

        # Бажарилган топшириқлар (completed, охирги 15 та)
        stmt_comp = select(Task).where(
            Task.assigned_telegram_id == tg_id,
            Task.status == "completed"
        ).order_by(Task.updated_at.desc(), Task.id.desc()).limit(15)
        res_comp = await session.execute(stmt_comp)
        completed_tasks = list(res_comp.scalars().all())

        return active_tasks, completed_tasks


async def render_active_tasks(chat_id: int, user_id: int, bot: Bot, edit_message_id: Optional[int] = None):
    """Фаол топшириқлар рўйхати ва ҳар бири остида «Бажардим» ҳамда «Изоҳ қолдириш» тугмалари."""
    active_tasks, completed_tasks = await get_employee_tasks(user_id)
    comp_cnt = len(completed_tasks)

    if not active_tasks:
        text = (
            "⏳ <b>ФАОЛ ТОПШИРИҚЛАР</b>\n\n"
            "Сизда ҳозирда ижро этилиши керак бўлган фаол топшириқлар мавжуд эмас.\n"
            "Барча вазифалар муваффақиятли бажарилган! 🎉"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"📁 Бажарилган топшириқлар ({comp_cnt})", callback_data="tasks_completed"),
                InlineKeyboardButton(text="🔄 Янгилаш", callback_data="tasks_refresh_active"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Бўлимлар менюси", callback_data="tasks_menu")
            ]
        ])
        if edit_message_id:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=text, reply_markup=kb, parse_mode="HTML")
                return
            except Exception:
                pass
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")
        return

    header_text = (
        f"⏳ <b>СИЗНИНГ ФАОЛ ТОПШИРИҚЛАРИНГИЗ ({len(active_tasks)} та)</b>\n"
        f"<i>(Ижро этилмаган ва муддати келаётган вазифалар):</i>"
    )
    if edit_message_id:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=header_text, parse_mode="HTML")
        except Exception:
            await bot.send_message(chat_id=chat_id, text=header_text, parse_mode="HTML")
    else:
        await bot.send_message(chat_id=chat_id, text=header_text, parse_mode="HTML")

    for t in active_tasks[:10]:
        status_icon = "🆕" if t.status == "new" else "🔄"
        status_name = "Янги" if t.status == "new" else "Жараёнда"
        deadline_str = format_task_deadline(t.deadline)
        voice_badge = " 🎙 (Овозли топшириқ)" if t.voice_url else ""
        creator_str = t.creator_name or "Раҳбарият"

        card_text = (
            f"{status_icon} <b>ТОПШИРИҚ #{t.id}</b>{voice_badge}\n\n"
            f"📌 <b>Мавзу:</b> {t.title}\n"
            f"📝 <b>Тафсилот:</b> {t.description or '—'}\n"
            f"⏰ <b>Муддат:</b> {deadline_str}\n"
            f"📊 <b>Ҳолат:</b> {status_name}\n"
            f"👤 <b>Буюрди:</b> {creator_str}"
        )
        if t.employee_response:
            card_text += f"\n💬 <b>Сизнинг изоҳингиз:</b> <i>{t.employee_response}</i>"

        task_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Бажардим", callback_data=f"task_done_{t.id}"),
                InlineKeyboardButton(text="✍️ Изоҳ қолдириш", callback_data=f"task_respond_{t.id}"),
            ]
        ])
        await bot.send_message(chat_id=chat_id, text=card_text, reply_markup=task_kb, parse_mode="HTML")

    nav_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"📁 Бажарилган топшириқлар ({comp_cnt})", callback_data="tasks_completed"),
            InlineKeyboardButton(text="🔄 Янгилаш", callback_data="tasks_refresh_active"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Бўлимлар менюси", callback_data="tasks_menu")
        ]
    ])
    await bot.send_message(chat_id=chat_id, text="<i>Бошқа бўлимга ўтиш учун:</i>", reply_markup=nav_kb, parse_mode="HTML")


async def render_completed_tasks(chat_id: int, user_id: int, bot: Bot, edit_message_id: Optional[int] = None):
    """Бажарилган топшириқлар архивини чиқариш."""
    active_tasks, completed_tasks = await get_employee_tasks(user_id)
    act_cnt = len(active_tasks)

    if not completed_tasks:
        text = (
            "📁 <b>БАЖАРИЛГАН ТОПШИРИҚЛАР АРХИВИ</b>\n\n"
            "Ҳозирча сиз томонингиздан бажарилган топшириқлар архиви бўш."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"⏳ Фаол топшириқлар ({act_cnt})", callback_data="tasks_active"),
                InlineKeyboardButton(text="🔄 Янгилаш", callback_data="tasks_refresh_completed"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Бўлимлар менюси", callback_data="tasks_menu")
            ]
        ])
        if edit_message_id:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=text, reply_markup=kb, parse_mode="HTML")
                return
            except Exception:
                pass
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")
        return

    lines = [f"📁 <b>БАЖАРИЛГАН ТОПШИРИҚЛАР АРХИВИ ({len(completed_tasks)} та)</b>:\n"]
    for idx, t in enumerate(completed_tasks[:10], 1):
        done_time = t.updated_at
        if done_time:
            if done_time.tzinfo is None:
                done_time = done_time.replace(tzinfo=timezone.utc).astimezone(UZ_TZ)
            done_str = done_time.strftime("%d.%m.%Y %H:%M")
        else:
            done_str = "—"

        lines.append(
            f"✅ <b>#{t.id} — {t.title}</b>\n"
            f"   ⏰ Бажарилди: {done_str}\n"
            f"   📝 Тафсилот: {t.description or '—'}"
        )
        if t.employee_response:
            lines.append(f"   💬 Сизнинг изоҳингиз: <i>{t.employee_response}</i>")
        lines.append("")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"⏳ Фаол топшириқлар ({act_cnt})", callback_data="tasks_active"),
            InlineKeyboardButton(text="🔄 Янгилаш", callback_data="tasks_refresh_completed"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Бўлимлар менюси", callback_data="tasks_menu")
        ]
    ])
    text = "\n".join(lines)
    if edit_message_id:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("tasks"))
@router.message(F.text.contains("Менинг топшириқларим"))
async def cmd_tasks(message: types.Message):
    """«Менинг топшириқларим» бўлими: Фаол ва Бажарилган топшириқлар танлови."""
    emp = await get_authorized_employee(message.from_user.id, message.from_user.username)
    if not emp:
        await message.answer(
            "⚠️ <b>Сиз ҳали авторизациядан ўтмагансиз!</b>\n"
            "Илтимос, аввал телефон рақамингизни юбориб шахсингизни тасдиқланг.",
            reply_markup=get_auth_keyboard(),
            parse_mode="HTML"
        )
        return

    active_tasks, completed_tasks = await get_employee_tasks(message.from_user.id)
    act_cnt = len(active_tasks)
    comp_cnt = len(completed_tasks)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"⏳ Фаол топшириқлар ({act_cnt})", callback_data="tasks_active"),
            InlineKeyboardButton(text=f"📁 Бажарилган топшириқлар ({comp_cnt})", callback_data="tasks_completed"),
        ]
    ])

    text = (
        f"📋 <b>ТОПШИРИҚЛАР БЎЛИМИ</b>\n\n"
        f"👤 <b>Ходим:</b> {emp.employee_name}\n\n"
        f"⏳ <b>Фаол топшириқлар:</b> {act_cnt} та\n"
        f"📁 <b>Бажарилганлар (архив):</b> {comp_cnt} та\n\n"
        f"<i>Кўрмоқчи бўлган бўлимингизни танланг:</i>"
    )
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.in_(["tasks_active", "tasks_refresh_active"]))
async def cb_tasks_active(callback: CallbackQuery):
    """Инлайн тугма орқали фаол топшириқларни кўриш."""
    await callback.answer()
    await render_active_tasks(
        chat_id=callback.message.chat.id,
        user_id=callback.from_user.id,
        bot=callback.bot,
        edit_message_id=callback.message.message_id
    )


@router.callback_query(F.data.in_(["tasks_completed", "tasks_refresh_completed"]))
async def cb_tasks_completed(callback: CallbackQuery):
    """Инлайн тугма орқали бажарилган топшириқларни кўриш."""
    await callback.answer()
    await render_completed_tasks(
        chat_id=callback.message.chat.id,
        user_id=callback.from_user.id,
        bot=callback.bot,
        edit_message_id=callback.message.message_id
    )


@router.callback_query(F.data == "tasks_menu")
async def cb_tasks_menu(callback: CallbackQuery):
    """Бўлимлар бош менюсига қайтиш."""
    await callback.answer()
    emp = await get_authorized_employee(callback.from_user.id, callback.from_user.username)
    active_tasks, completed_tasks = await get_employee_tasks(callback.from_user.id)
    act_cnt = len(active_tasks)
    comp_cnt = len(completed_tasks)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"⏳ Фаол топшириқлар ({act_cnt})", callback_data="tasks_active"),
            InlineKeyboardButton(text=f"📁 Бажарилган топшириқлар ({comp_cnt})", callback_data="tasks_completed"),
        ]
    ])

    text = (
        f"📋 <b>ТОПШИРИҚЛАР БЎЛИМИ</b>\n\n"
        f"👤 <b>Ходим:</b> {emp.employee_name if emp else 'Ходим'}\n\n"
        f"⏳ <b>Фаол топшириқлар:</b> {act_cnt} та\n"
        f"📁 <b>Бажарилганлар (архив):</b> {comp_cnt} та\n\n"
        f"<i>Кўрмоқчи бўлган бўлимингизни танланг:</i>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text.contains("Фаол топшириқлар"))
async def msg_tasks_active(message: types.Message):
    """Матн орқали фаол топшириқларни сўраш."""
    await render_active_tasks(chat_id=message.chat.id, user_id=message.from_user.id, bot=message.bot)


@router.message(F.text.contains("Бажарилган топшириқлар"))
async def msg_tasks_completed(message: types.Message):
    """Матн орқали бажарилган топшириқларни сўраш."""
    await render_completed_tasks(chat_id=message.chat.id, user_id=message.from_user.id, bot=message.bot)


@router.message(F.text.contains("Янги топшириқ"))
async def btn_new_task(message: types.Message):
    """Янги топшириқ тугмаси босилганда йўриқнома ва имкониятларни кўрсатиш."""
    emp = await get_authorized_employee(message.from_user.id, message.from_user.username)
    if not emp:
        await message.answer(
            "⚠️ <b>Сиз ҳали авторизациядан ўтмагансиз!</b>\n"
            "Илтимос, аввал телефон рақамингизни юбориб шахсингизни тасдиқланг.",
            reply_markup=get_auth_keyboard(),
            parse_mode="HTML"
        )
        return

    text = (
        "➕ <b>Янги топшириқ яратиш бўйича кўрсатма</b>\n\n"
        "Сиз ходимларга топшириқни 2 хил тезкор усулда юборишингиз мумкин:\n\n"
        "1. 🎙 <b>Овозли хабар (Voice) орқали:</b>\n"
        "Telegram микрофонини босиб, ходим исми ва вазифасини гапиринг. Масалан:\n"
        "<i>«Латипов, дўкондаги қолдиқларни санаб чиқинг, соат 18:00 гача»</i>\n"
        "ИИ тизими автоматик равишда ижрочини ва муддатни аниқлайди ҳамда топшириқ яратади.\n\n"
        "2. ✍️ <b>Матнли хабар орқали:</b>\n"
        "Шунчаки ёзма хабар юборинг. Масалан:\n"
        "<i>«Каххоров, омбордаги янги партияни қабул қилинг»</i>\n\n"
        "🌐 <a href='https://santexnika.onrender.com/dashboard#tasks'>Дашбордда кўриш ва бошқариш</a>"
    )
    await message.answer(text, reply_markup=get_main_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("task_accept_"))
async def handle_task_accept(callback: CallbackQuery):
    """Ходим топшириқни қабул қилди — статусни in_progress га ўзгартириш."""
    await callback.answer("✅ Қабул қилинди!")

    task_id_str = callback.data.replace("task_accept_", "")
    try:
        task_id = int(task_id_str)
    except ValueError:
        return

    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            await callback.message.answer("❌ Топшириқ топилмади.")
            return

        if task.status == "new":
            task.status = "in_progress"
            task.updated_at = datetime.utcnow()
            await session.commit()

        deadline_str = format_task_deadline(task.deadline)

        # Янгиланган хабар
        text = (
            f"📋 <b>ТОПШИРИҚ #{task.id}</b> — 🟢 Қабул қилинди\n\n"
            f"📌 <b>Мавзу:</b> {task.title}\n"
            f"📝 <b>Тафсилот:</b> {task.description or '—'}\n"
            f"⏰ <b>Муддат:</b> {deadline_str}\n"
            f"📊 <b>Статус:</b> 🔄 Жараёнда\n"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Бажардим", callback_data=f"task_done_{task.id}"),
                InlineKeyboardButton(text="✍️ Изоҳ қолдириш", callback_data=f"task_respond_{task.id}")
            ]
        ])

        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            try:
                await callback.message.edit_caption(caption=text, reply_markup=keyboard, parse_mode="HTML")
            except Exception:
                await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    # Топшириқ берган шахсга хабар
    try:
        target_chat = task.creator_chat_id or os.getenv("TELEGRAM_CEO_CHAT_ID") or "5950380558"
        notify_text = (
            f"🟢 <b>Топшириқ #{task_id} қабул қилинди</b>\n\n"
            f"👤 <b>Ижрочи:</b> {task.assigned_name}\n"
            f"📌 <b>Мавзу:</b> {task.title}"
        )
        await callback.bot.send_message(chat_id=target_chat, text=notify_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Раҳбарга accept хабари юборишда хатолик: {e}")


@router.callback_query(F.data.startswith("task_done_"))
async def handle_task_done(callback: CallbackQuery):
    """Ходим топшириқни бажарди — статус completed, фаол рўйхатдан чиқариш ва раҳбарга хабар."""
    await callback.answer("✅ Топшириқ бажарилди деб белгиланди!")

    task_id_str = callback.data.replace("task_done_", "")
    try:
        task_id = int(task_id_str)
    except ValueError:
        return

    now_local = datetime.now(UZ_TZ)

    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            await callback.message.answer("❌ Топшириқ топилмади.")
            return

        task.status = "completed"
        task.updated_at = datetime.utcnow()
        await session.commit()

        # Update remaining active tasks count
        stmt_act = select(Task).where(
            Task.assigned_telegram_id == callback.from_user.id,
            Task.status.in_(["new", "in_progress"])
        )
        res_act = await session.execute(stmt_act)
        remaining_cnt = len(res_act.scalars().all())

        text = (
            f"✅ <b>ТОПШИРИҚ БАЖАРИЛДИ! #{task.id}</b>\n\n"
            f"📌 <b>Мавзу:</b> {task.title}\n"
            f"📝 <b>Тафсилот:</b> {task.description or '—'}\n"
            f"📊 <b>Статус:</b> 🟢 Бажарилди (Архивга ўтказилди)\n"
            f"⏰ <b>Бажарилган вақт:</b> {now_local.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"<i>Топшириқ фаол рўйхатдан чиқарилиб, «Бажарилганлар» бўлимига ўтказилди.</i>"
        )
        if task.employee_response:
            text += f"\n💬 <b>Сизнинг изоҳингиз:</b> <i>{task.employee_response}</i>"

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"⏳ Фаол топшириқлар ({remaining_cnt})", callback_data="tasks_active"),
                InlineKeyboardButton(text="📁 Бажарилганлар", callback_data="tasks_completed"),
            ]
        ])

        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            try:
                await callback.message.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")

        # 1. Топшириқ берган шахсга (creator_chat_id) хабар бериш
        target_chat = task.creator_chat_id or os.getenv("TELEGRAM_CEO_CHAT_ID") or "5950380558"
        try:
            creator_text = (
                f"🏁 <b>ВАЗИФА БАЖАРИЛДИ!</b>\n\n"
                f"Ходим <b>{task.assigned_name}</b> вазифани муваффақиятли бажарди.\n\n"
                f"📋 <b>Топшириқ #{task.id}:</b> {task.title}\n"
                f"⏰ <b>Бажарилган вақт:</b> {now_local.strftime('%d.%m.%Y %H:%M')}\n"
                f"🌐 <a href='https://santexnika.onrender.com/dashboard#tasks'>Дашбордда кўриш</a>"
            )
            await callback.bot.send_message(chat_id=target_chat, text=creator_text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Раҳбарга task_done хабари юборишда хатолик: {e}")

        # 2. Агар кузатувчи бўлса унга ҳам хабар
        if task.observer_telegram_id and str(task.observer_telegram_id) != str(target_chat):
            try:
                obs_text = (
                    f"👁 <b>НАЗОРАТ: Вазифа бажарилди</b>\n\n"
                    f"Ходим <b>{task.assigned_name}</b> вазифани бажарди.\n\n"
                    f"📋 <b>Топшириқ #{task.id}:</b> {task.title}\n"
                    f"⏰ <b>Бажарилган вақт:</b> {now_local.strftime('%d.%m.%Y %H:%M')}"
                )
                await callback.bot.send_message(chat_id=task.observer_telegram_id, text=obs_text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Кузатувчига task_done хабари юборишда хатолик: {e}")


@router.callback_query(F.data.startswith("task_respond_"))
async def handle_task_respond(callback: CallbackQuery, state: FSMContext):
    """Ходим топшириққа изоҳ/ҳисобот қолдирмоқчи — FSM га ўтказиш."""
    await callback.answer()

    task_id_str = callback.data.replace("task_respond_", "")
    try:
        task_id = int(task_id_str)
    except ValueError:
        return

    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            await callback.message.answer("❌ Топшириқ топилмади.")
            return

    await state.set_state(TaskStates.waiting_for_response)
    await state.update_data(task_id=task_id)

    prompt = (
        f"✍️ <b>Топшириқ #{task_id} га изоҳ қолдириш</b>\n\n"
        f"📌 <b>Мавзу:</b> {task.title}\n\n"
        f"Илтимос, изоҳ ёки ҳисоботингизни ёзиб юборинг:\n"
        f"<i>(Бекор қилиш учун «❌ Бекор қилиш» тугмасини босинг)</i>"
    )
    await callback.message.answer(
        prompt,
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Бекор қилиш")]],
            resize_keyboard=True,
            one_time_keyboard=True
        ),
        parse_mode="HTML"
    )


@router.message(TaskStates.waiting_for_response)
async def handle_task_response_text(message: types.Message, state: FSMContext):
    """Ходим изоҳ/жавоб матнини юборди — базага ёзиш ва раҳбарга хабар бериш."""
    if message.text in ("❌ Бекор қилиш", "/cancel", "Бекор қилиш"):
        await state.clear()
        await message.answer("❌ Изоҳ қолдириш бекор қилинди.", reply_markup=get_main_keyboard())
        return

    data = await state.get_data()
    task_id = data.get("task_id")
    await state.clear()

    if not task_id:
        await message.answer("❌ Топшириқ аниқланмади. Илтимос, қайта уриниб кўринг.", reply_markup=get_main_keyboard())
        return

    response_text = message.text or ""
    if not response_text.strip():
        await message.answer("⚠️ Изоҳ матни бўш. Илтимос, изоҳингизни ёзинг.", reply_markup=get_main_keyboard())
        return

    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            await message.answer("❌ Топшириқ топилмади.", reply_markup=get_main_keyboard())
            return

        task.employee_response = response_text
        if task.status == "new":
            task.status = "in_progress"
        task.updated_at = datetime.utcnow()
        await session.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Бажардим", callback_data=f"task_done_{task_id}"),
            InlineKeyboardButton(text="⏳ Фаол топшириқлар", callback_data="tasks_active"),
        ]
    ])

    await message.answer(
        f"✅ <b>Изоҳингиз муваффақиятли сақланди!</b>\n\n"
        f"📋 <b>Топшириқ:</b> #{task_id} — {task.title}\n"
        f"💬 <b>Изоҳ:</b> {response_text[:300]}{'...' if len(response_text) > 300 else ''}\n\n"
        f"<i>Изоҳ раҳбариятга ва дашбордга узатилди.</i>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await message.answer("Асосий меню:", reply_markup=get_main_keyboard())

    # Раҳбарга хабар
    try:
        ceo_chat_id = task.creator_chat_id or os.getenv("TELEGRAM_CEO_CHAT_ID") or "5950380558"
        deadline_str = format_task_deadline(task.deadline)

        notify_text = (
            f"📩 <b>ХОДИМ ИЗОҲИ — Топшириқ #{task_id}</b>\n\n"
            f"👤 <b>Ижрочи:</b> {task.assigned_name}\n"
            f"📌 <b>Мавзу:</b> {task.title}\n"
            f"⏰ <b>Муддат:</b> {deadline_str}\n"
            f"💬 <b>Изоҳ:</b>\n{response_text}\n"
            f"🌐 <a href='https://santexnika.onrender.com/dashboard#tasks'>Дашбордда кўриш</a>"
        )
        await message.bot.send_message(chat_id=ceo_chat_id, text=notify_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Раҳбарга изоҳ хабари юборишда хатолик: {e}")

    # Кузатувчига хабар
    if task.observer_telegram_id and str(task.observer_telegram_id) != str(ceo_chat_id):
        try:
            observer_text = (
                f"📩 <b>НАЗОРАТ: Ходим изоҳ қолдирди</b>\n\n"
                f"📋 <b>Топшириқ:</b> #{task_id} — {task.title}\n"
                f"👤 <b>Ижрочи:</b> {task.assigned_name}\n"
                f"💬 <b>Изоҳ:</b>\n{response_text}"
            )
            await message.bot.send_message(
                chat_id=task.observer_telegram_id,
                text=observer_text,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Кузатувчига изоҳ хабари юборишда хатолик: {e}")


# ═══════════════ ОВОЗЛИ ВА ЁЗМА ТОПШИРИҚЛАРНИ ҚАБУЛ ҚИЛИШ ═══════════════

@router.message(F.voice)
async def handle_voice_task(message: types.Message):
    """
    Раҳбар ёки менежер ботга овозли хабар юборганда:
    1. Овозни сақлаш ва Faster-Whisper / OpenAI STT орқали матнга ўгириш.
    2. Матндан ижрочи (authorized_employees), топшириқ мазмуни ва муддатни ажратиб олиш.
    3. Топшириқни tasks жадвалига сақлаш ва ижрочи ходимга Telegram орқали овозли хабар юбориш.
    """
    emp = await get_authorized_employee(message.from_user.id)
    creator_name = emp.employee_name if emp else (message.from_user.full_name or "Раҳбарият")

    wait_msg = await message.answer("🎙 <i>Овозли хабар қабул қилинди. Таҳлил қилинмоқда, илтимос кутинг...</i>", parse_mode="HTML")

    try:
        import uuid
        import time
        from pathlib import Path
        from services.task_parser import transcribe_audio, parse_task_instruction

        upload_dir = Path(BASE_DIR) / "static" / "uploads" / "voice_tasks"
        upload_dir.mkdir(parents=True, exist_ok=True)
        unique_name = f"bot_voice_{uuid.uuid4().hex[:12]}_{int(time.time())}.ogg"
        voice_disk_path = upload_dir / unique_name

        # Овозни серверга юклаб олиш
        await message.bot.download(message.voice, destination=voice_disk_path)

        # STT транскрипция (Faster-Whisper / OpenAI)
        transcribed_text = transcribe_audio(str(voice_disk_path))

        if not transcribed_text or not transcribed_text.strip():
            await wait_msg.edit_text(
                "⚠️ <b>Овозни матнга ўгиришнинг иложи бўлмади.</b>\n"
                "Илтимос, тиниқроқ овоз ёзиб ёки матн кўринишида қайта юборинг.",
                parse_mode="HTML"
            )
            return

        # Базадаги фаол ходимлар рўйхати билан солиштириш
        async with AsyncSessionLocal() as session:
            stmt = select(AuthorizedEmployee).where(AuthorizedEmployee.is_active == 1)
            res = await session.execute(stmt)
            employees = res.scalars().all()

            parsed = parse_task_instruction(transcribed_text, employees)
            assignee = parsed.get("assignee")

            if not assignee:
                await wait_msg.edit_text(
                    f"🎙 <b>Овозли хабар матнга ўгирилди:</b>\n\n"
                    f"<i>«{transcribed_text}»</i>\n\n"
                    f"⚠️ <b>Ижрочи ходим аниқланмади.</b>\n"
                    f"Илтимос, топшириқ бераётганда ходим исмини ҳам айтинг (масалан: <i>«Латипов, дўкондаги қолдиқларни ҳисоблагин...»</i> ёки <i>«Джумаева, шартномаларни тайёрланг...»</i>).",
                    parse_mode="HTML"
                )
                return

            task_title = parsed["task_text"][:250] if parsed["task_text"] else transcribed_text[:250]
            task_desc = transcribed_text
            voice_url = f"/static/uploads/voice_tasks/{unique_name}"

            task = Task(
                title=task_title,
                description=task_desc,
                creator_name=creator_name,
                creator_chat_id=message.from_user.id,
                assigned_to=assignee.id,
                assigned_name=assignee.employee_name,
                assigned_telegram_id=assignee.telegram_id,
                deadline=parsed["deadline_dt"],
                status="new",
                voice_url=voice_url,
                voice_file_id=message.voice.file_id,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)

            # Ижрочига овозли хабар ва инлайн тугмаларни юбориш
            if assignee.telegram_id:
                assignee_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🟢 Қабул қилдим", callback_data=f"task_accept_{task.id}"),
                        InlineKeyboardButton(text="✅ Бажардим", callback_data=f"task_done_{task.id}"),
                    ],
                    [
                        InlineKeyboardButton(text="✍️ Изоҳ қолдириш", callback_data=f"task_respond_{task.id}"),
                    ]
                ])
                caption = (
                    f"🎙 <b>СИЗГА ЯНГИ ОВОЗЛИ ТОПШИРИҚ БЕРИЛДИ! #{task.id}</b>\n\n"
                    f"👤 <b>Ким берди:</b> {creator_name}\n"
                    f"📌 <b>Мавзу:</b> {task.title}\n"
                    f"⏰ <b>Муддат:</b> {parsed['deadline_str']}\n"
                    f"💬 <b>Матн:</b> <i>«{transcribed_text}»</i>\n\n"
                    f"<i>Илтимос, вазифани ўз вақтида бажаринг!</i>"
                )
                try:
                    await message.bot.send_voice(
                        chat_id=assignee.telegram_id,
                        voice=message.voice.file_id,
                        caption=caption,
                        reply_markup=assignee_kb,
                        parse_mode="HTML"
                    )
                    logger.info(f"Овозли топшириқ {assignee.employee_name} га муваффақиятли юборилди")
                except Exception as ex:
                    logger.warning(f"Ижрочига овоз юборишда хатолик: {ex}")
                    await message.bot.send_message(
                        chat_id=assignee.telegram_id,
                        text=caption,
                        reply_markup=assignee_kb,
                        parse_mode="HTML"
                    )

            # Топшириқ берган шахсга тасдиқ
            confirm_text = (
                f"✅ <b>Овозли топшириқ муваффақиятли яратилди! #{task.id}</b>\n\n"
                f"👤 <b>Ижрочи:</b> {assignee.employee_name}\n"
                f"📌 <b>Мазмуни:</b> {task.title}\n"
                f"⏰ <b>Муддати:</b> {parsed['deadline_str']}\n"
                f"💬 <b>Транскрипция:</b> <i>«{transcribed_text}»</i>\n\n"
                f"🚀 <i>Ижрочининг шахсий Telegram'ига юборилди ва дашбордга киритилди!</i>\n"
                f"🌐 <a href='https://santexnika.onrender.com/dashboard#tasks'>Дашбордда кўриш</a>"
            )
            await wait_msg.edit_text(confirm_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Voice task error: {e}")
        await wait_msg.edit_text(f"❌ Овозли топшириқ яратишда хатолик юз берди: {e}")


@router.message(
    F.text 
    & ~F.text.startswith("/") 
    & ~F.text.contains("Ишга келиш") 
    & ~F.text.contains("Ишга келдим") 
    & ~F.text.contains("Ишдан кетиш") 
    & ~F.text.contains("Ишдан кетдим") 
    & ~F.text.contains("Менинг топшириқларим")
    & ~F.text.contains("Янги топшириқ")
    & ~F.text.contains("Бекор қилиш")
)
async def handle_text_task_or_query(message: types.Message, state: FSMContext):
    """
    Раҳбар ёки ходим матнли топшириқ юборганда:
    Агар матнда ходим исми (масалан: 'Латипов, ...') бўлса, топшириқ яратилади ва ижрочига юборилади.
    """
    curr_state = await state.get_state()
    if curr_state:
        # Агар FSM да бўлса (масалан: жавоб ёзиш) тегишли handler ишласин
        return

    text = message.text.strip()
    if len(text) < 5:
        return

    try:
        from services.task_parser import parse_task_instruction

        async with AsyncSessionLocal() as session:
            stmt = select(AuthorizedEmployee).where(AuthorizedEmployee.is_active == 1)
            res = await session.execute(stmt)
            employees = res.scalars().all()

            parsed = parse_task_instruction(text, employees)
            assignee = parsed.get("assignee")

            # Агар ходим номи топилмаса, шунчаки ўтказиб юборамиз
            if not assignee:
                return

            emp = await get_authorized_employee(message.from_user.id, message.from_user.username)
            creator_name = emp.employee_name if emp else (message.from_user.full_name or "Раҳбарият")

            task_title = parsed["task_text"][:250] if parsed["task_text"] else text[:250]
            task_desc = text

            task = Task(
                title=task_title,
                description=task_desc,
                creator_name=creator_name,
                creator_chat_id=message.from_user.id,
                assigned_to=assignee.id,
                assigned_name=assignee.employee_name,
                assigned_telegram_id=assignee.telegram_id,
                deadline=parsed["deadline_dt"],
                status="new",
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)

            # Ижрочига хабар юбориш
            if assignee.telegram_id:
                assignee_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🟢 Қабул қилдим", callback_data=f"task_accept_{task.id}"),
                        InlineKeyboardButton(text="✅ Бажардим", callback_data=f"task_done_{task.id}"),
                    ],
                    [
                        InlineKeyboardButton(text="✍️ Изоҳ қолдириш", callback_data=f"task_respond_{task.id}"),
                    ]
                ])
                msg = (
                    f"📋 <b>СИЗГА ЯНГИ ТОПШИРИҚ БЕРИЛДИ! #{task.id}</b>\n\n"
                    f"👤 <b>Ким берди:</b> {creator_name}\n"
                    f"📌 <b>Мавзу:</b> {task.title}\n"
                    f"⏰ <b>Муддат:</b> {parsed['deadline_str']}\n"
                    f"📝 <b>Тафсилот:</b> {task.description}\n\n"
                    f"<i>Илтимос, вазифани ўз вақтида бажаринг!</i>"
                )
                try:
                    await message.bot.send_message(
                        chat_id=assignee.telegram_id,
                        text=msg,
                        reply_markup=assignee_kb,
                        parse_mode="HTML"
                    )
                except Exception as ex:
                    logger.warning(f"Ижрочига матнли топшириқ юборишда хатолик: {ex}")

            # Раҳбарга тасдиқ хабари
            confirm_text = (
                f"✅ <b>Топшириқ яратилди ва юборилди! #{task.id}</b>\n\n"
                f"👤 <b>Ижрочи:</b> {assignee.employee_name}\n"
                f"📌 <b>Мазмуни:</b> {task.title}\n"
                f"⏰ <b>Муддати:</b> {parsed['deadline_str']}\n\n"
                f"🚀 <i>Ижрочининг шахсий Telegram'ига юборилди ва дашбордга киритилди!</i>\n"
                f"🌐 <a href='https://santexnika.onrender.com/dashboard#tasks'>Дашбордда кўриш</a>"
            )
            await message.answer(confirm_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Text task error: {e}")


# ═══════════════ ASOSIY ISHGA TUSHIRISH FUNKSIYASI ═══════════════

bot_instance: Optional[Bot] = None
dp_instance: Optional[Dispatcher] = None

async def run_bot_polling():
    """Ботнинг доимий (24/7) ишлаши ва қайта уланишини таъминловчи корутина."""
    global bot_instance, dp_instance
    token = getattr(settings, "telegram_bot_token", None) or os.getenv("TELEGRAM_BOT_TOKEN", "8859657582:AAE6oCILrzGUOydYSPNdpDckuUy5pcv4gIc")
    if not token or token == "test_telegram_bot_token":
        token = "8859657582:AAE6oCILrzGUOydYSPNdpDckuUy5pcv4gIc"
    if not token or token.startswith("test_"):
        logger.warning("TELEGRAM_BOT_TOKEN топилмади ёки тест ҳолатида, бот ишга туширилмади.")
        return

    from aiogram.exceptions import TelegramConflictError

    # Ma'lumotlar bazasi jadvallarini tekshirish va ustunlarni avto-yangilash
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            def _migrate_tasks_cols(sync_conn):
                try:
                    cursor = sync_conn.connection.cursor()
                    cursor.execute("PRAGMA table_info(tasks);")
                    cols = {row[1] for row in cursor.fetchall()}
                    if cols:
                        if "creator_name" not in cols:
                            cursor.execute("ALTER TABLE tasks ADD COLUMN creator_name VARCHAR(255);")
                        if "creator_chat_id" not in cols:
                            cursor.execute("ALTER TABLE tasks ADD COLUMN creator_chat_id BIGINT;")
                        if "voice_file_id" not in cols:
                            cursor.execute("ALTER TABLE tasks ADD COLUMN voice_file_id VARCHAR(255);")
                        if "voice_url" not in cols:
                            cursor.execute("ALTER TABLE tasks ADD COLUMN voice_url VARCHAR(500);")
                except Exception as ex:
                    logger.warning(f"tasks columns migration warning: {ex}")
            await conn.run_sync(_migrate_tasks_cols)
    except Exception as dbe:
        logger.warning(f"Database schema migration warning: {dbe}")

    bot_instance = Bot(token=token)
    dp_instance = Dispatcher(storage=MemoryStorage())
    dp_instance.include_router(router)

    try:
        me = await bot_instance.get_me()
        logger.info(f"🤖 Diyor Group Telegram Bot ишга тушди: @{me.username} ({me.full_name})")
    except Exception as e:
        logger.error(f"Бот маълумотларини олишда хатолик: {e}")

    backoff = 5
    while True:
        try:
            # drop_pending_updates=False: ходимларнинг юборган /start ёки топшириқлари йўқолмайди!
            await bot_instance.delete_webhook(drop_pending_updates=False)
            logger.info("📡 Telegram Bot polling бошланди...")
            await dp_instance.start_polling(bot_instance, allowed_updates=["message", "callback_query"])
            backoff = 5
        except asyncio.CancelledError:
            logger.info("🛑 Telegram Bot вазифаси бекор қилинди (Cancelled).")
            break
        except TelegramConflictError:
            logger.warning("⚠️ ConflictError: Бот бошқа сессияда (бошқа сервер ёки тест жараёнида) polling қилмоқда. 25 сония кутилмоқда...")
            await asyncio.sleep(25)
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            logger.error(f"Бот тармоғида узилиш: {e}. {backoff} сониядан сўнг қайта уланади...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    try:
        if bot_instance and bot_instance.session:
            await bot_instance.session.close()
    except Exception:
        pass


async def main():
    await run_bot_polling()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n🛑 Бот тўхтатилди.")
