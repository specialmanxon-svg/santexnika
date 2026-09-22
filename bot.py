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


async def notify_management(bot: Bot, text: str):
    """Раҳбарият гуруҳи ва каналига хабар юбориш."""
    chat_ids = set()
    group_id = os.getenv("TELEGRAM_GROUP_ID") or getattr(settings, "telegram_group_id", None)
    if group_id and str(group_id).strip():
        chat_ids.add(str(group_id).strip())

    alert_id = getattr(settings, "TELEGRAM_ALERT_CHAT_ID", None) or os.getenv("TELEGRAM_ALERT_CHAT_ID")
    if alert_id and str(alert_id).strip():
        chat_ids.add(str(alert_id).strip())

    ceo_id = getattr(settings, "TELEGRAM_CEO_CHAT_ID", None) or os.getenv("TELEGRAM_CEO_CHAT_ID")
    if ceo_id and str(ceo_id).strip():
        chat_ids.add(str(ceo_id).strip())

    chat_ids.add("5950380558")  # Feruz Latipov (@Diyor_manager)

    for cid in chat_ids:
        try:
            await bot.send_message(chat_id=cid, text=text, parse_mode="HTML")
            logger.info(f"Раҳбариятга хабар юборилди: {cid}")
        except Exception as ex:
            logger.warning(f"Раҳбариятга хабар юборишда хатолик ({cid}): {ex}")


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
        resize_keyboard=True
    )


def get_location_keyboard() -> ReplyKeyboardMarkup:
    """GPS геолокацияни сўраш клавиатураси."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Ҳозирги геолокацияни юбориш", request_location=True)],
            [KeyboardButton(text="❌ Бекор қилиш")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


# ═══════════════ FSM STATES ═══════════════

class AttendanceStates(StatesGroup):
    waiting_for_location = State()


class TaskStates(StatesGroup):
    waiting_for_response = State()

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


@router.message(F.text.contains("Ишга келдим") | (F.text == "🟢 Ишга келдим (GPS)"))
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
        f"Ҳурматли <b>{emp.employee_name}</b>!\n"
        f"Илтимос, пастдаги «📍 Ҳозирги геолокацияни юбориш» тугмасини босинг:"
    )
    await message.answer(prompt, reply_markup=get_location_keyboard(), parse_mode="HTML")


@router.message(F.text.contains("Ишдан кетдим") | (F.text == "🔴 Ишдан кетдим (GPS)"))
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
        f"Ҳурматли <b>{emp.employee_name}</b>!\n"
        f"Илтимос, пастдаги «📍 Ҳозирги геолокацияни юбориш» тугмасини босинг:"
    )
    await message.answer(prompt, reply_markup=get_location_keyboard(), parse_mode="HTML")


@router.message(F.text.contains("Бекор қилиш") | (F.text == "❌ Бекор қилиш"))
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
                f"❌ <b>Ишга келиш қайд этилмади!</b>\n\n"
                f"Сиз иш жойида эмассиз.\n"
                f"🏢 <b>Объект:</b> {target_name}\n"
                f"📏 <b>Масофа:</b> {int(min_dist)} метр\n"
                f"⭕️ <b>Рухсат этилган радиус:</b> {int(allowed_radius)} метр\n\n"
                f"<i>Илтимос, дўкон ёки объект ҳудудига яқин келиб қайта уриниб кўринг!</i>"
            )
        else:
            fail_msg = (
                f"❌ <b>Ишдан кетиш қайд этилмади!</b>\n\n"
                f"Сиз иш жойида эмассиз.\n"
                f"🏢 <b>Объект:</b> {target_name}\n"
                f"📏 <b>Масофа:</b> {int(min_dist)} метр\n"
                f"⭕️ <b>Рухсат этилган радиус:</b> {int(allowed_radius)} метр\n\n"
                f"<i>Илтимос, ишдан кетишни қайд этиш учун объект ҳудудида туриб тугмани босинг!</i>"
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
                    f"✅ <b>Ишга келиш муваффақиятли қайд этилди!</b>\n\n"
                    f"👤 <b>Ходим:</b> {emp.employee_name}\n"
                    f"🏢 <b>Объект:</b> <b>{target_name}</b>\n"
                    f"⏰ <b>Вақт:</b> {now_local.strftime('%H:%M:%S')} ({now_local.strftime('%d.%m.%Y')})\n"
                    f"📏 <b>Масофа:</b> {int(min_dist)} метр\n"
                    f"📊 <b>Ҳолат:</b> {status_label}\n\n"
                    f"<i>Давомад бошқарув панели (Dashboard) га узатилди. Яхши иш куни тилаймиз!</i>"
                )

                # 3. Раҳбарият гуруҳига билдиришнома юбориш
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
                asyncio.create_task(notify_management(message.bot, mgmt_text))

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
                    f"🏁 <b>Иш сменаси муваффақиятли якунланди!</b>\n\n"
                    f"👤 <b>Ходим:</b> {emp.employee_name}\n"
                    f"🏢 <b>Объект:</b> <b>{target_name}</b>\n"
                    f"⏰ <b>Кетиш вақти:</b> {now_local.strftime('%H:%M:%S')}\n"
                    f"⏱ <b>Ишланган вақт:</b> {hours} соат\n\n"
                    f"<i>Давомад дашбордда акс эттирилди. Ҳорманг!</i>"
                )

                # 3. Раҳбарият гуруҳига билдиришнома юбориш
                mgmt_text = (
                    f"🏁 <b>Иш якунланди (GPS)</b>\n\n"
                    f"👤 <b>Ходим:</b> {emp.employee_name}\n"
                    f"🏢 <b>Объект:</b> {target_name}\n"
                    f"⏰ <b>Кетган вақти:</b> {now_local.strftime('%H:%M:%S')} ({now_local.strftime('%d.%m.%Y')})\n"
                    f"⏱ <b>Ишланган вақт:</b> {hours} соат\n"
                    f"📱 <b>Манба:</b> 📱 Telegram"
                )
                asyncio.create_task(notify_management(message.bot, mgmt_text))

        await message.answer(resp_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Давомадни сақлашда хатолик: {e}")

        await message.answer(resp_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Давомадни сақлашда хатолик: {e}")
        await message.answer(
            f"⚠️ Маълумотни сақлашда техник хатолик юз берди: {e}",
            reply_markup=get_main_keyboard()
        )


# ═══════════════ ТОПШИРИҚЛАР БЎЛИМИ (TASK MANAGER) ═══════════════

@router.message(Command("tasks"))
async def cmd_tasks(message: types.Message):
    """Ходимнинг фаол топшириқлари рўйхати."""
    emp = await get_authorized_employee(message.from_user.id)
    if not emp:
        await message.answer(
            "⚠️ <b>Сиз ҳали авторизациядан ўтмагансиз!</b>\n"
            "Илтимос, аввал телефон рақамингизни юбориб шахсингизни тасдиқланг.",
            reply_markup=get_auth_keyboard(),
            parse_mode="HTML"
        )
        return

    async with AsyncSessionLocal() as session:
        stmt = select(Task).where(
            Task.assigned_telegram_id == message.from_user.id,
            Task.status.in_(["new", "in_progress"]),
        ).order_by(Task.created_at.desc())
        result = await session.execute(stmt)
        tasks = result.scalars().all()

    if not tasks:
        await message.answer(
            "📋 <b>Сизда ҳозирча фаол топшириқлар мавжуд эмас.</b>",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        return

    text_lines = ["📋 <b>Сизнинг фаол топшириқларингиз:</b>\n"]
    for t in tasks:
        status_icon = {"new": "🆕", "in_progress": "🔄"}.get(t.status, "📋")
        deadline_str = "—"
        if t.deadline:
            dl = t.deadline
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc).astimezone(UZ_TZ)
            deadline_str = dl.strftime("%d.%m.%Y %H:%M")
        voice_badge = " 🎙 (Овозли)" if t.voice_url else ""
        text_lines.append(
            f"{status_icon} <b>#{t.id}</b> — {t.title}{voice_badge}\n"
            f"   ⏰ Муддат: {deadline_str}\n"
        )

    # Inline тугмалар — ҳар бир топшириққа жавоб бериш
    buttons = []
    for t in tasks[:10]:  # Максимум 10 та
        buttons.append([
            InlineKeyboardButton(
                text=f"✍️ #{t.id} га жавоб",
                callback_data=f"task_respond_{t.id}",
            )
        ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(
        "\n".join(text_lines),
        reply_markup=keyboard,
        parse_mode="HTML"
    )


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

        deadline_str = "—"
        if task.deadline:
            dl = task.deadline
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc).astimezone(UZ_TZ)
            deadline_str = dl.strftime("%d.%m.%Y %H:%M")

        # Янгиланган хабар
        text = (
            f"📋 <b>ТОПШИРИҚ #{task.id}</b> — ✅ Қабул қилинди\n\n"
            f"📌 <b>Мавзу:</b> {task.title}\n"
            f"📝 <b>Тафсилот:</b> {task.description or '—'}\n"
            f"⏰ <b>Муддат:</b> {deadline_str}\n"
            f"📊 <b>Статус:</b> 🔄 Жараёнда\n"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Жавоб/Ҳисобот юбориш", callback_data=f"task_respond_{task.id}")]
        ])

        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    # Раҳбарга хабар
    try:
        ceo_chat_id = os.getenv("TELEGRAM_CEO_CHAT_ID") or "5950380558"
        notify_text = (
            f"✅ <b>Топшириқ #{task_id} қабул қилинди</b>\n"
            f"👤 Ижрочи: {task.assigned_name}\n"
            f"📌 Мавзу: {task.title}"
        )
        await callback.bot.send_message(chat_id=ceo_chat_id, text=notify_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Раҳбарга accept хабари юборишда хатолик: {e}")


@router.callback_query(F.data.startswith("task_respond_"))
async def handle_task_respond(callback: CallbackQuery, state: FSMContext):
    """Ходим жавоб/ҳисобот юбормоқчи — FSM га ўтказиш."""
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

    await callback.message.answer(
        f"✍️ <b>Топшириқ #{task_id}</b> учун жавоб/ҳисоботингизни ёзинг:\n\n"
        f"📌 <b>Мавзу:</b> {task.title}\n\n"
        f"Қуйидаги хабарда жавобингизни матн кўринишида юборинг:",
        parse_mode="HTML"
    )


@router.message(TaskStates.waiting_for_response)
async def handle_task_response_text(message: types.Message, state: FSMContext):
    """Ходим жавоб матнини юборди — базага ёзиш ва раҳбарга хабар бериш."""
    data = await state.get_data()
    task_id = data.get("task_id")
    await state.clear()

    if not task_id:
        await message.answer("❌ Топшириқ аниқланмади. Илтимос, қайта уриниб кўринг.", reply_markup=get_main_keyboard())
        return

    response_text = message.text or ""
    if not response_text.strip():
        await message.answer("⚠️ Жавоб матни бўш. Илтимос, ҳисоботингизни ёзинг.", reply_markup=get_main_keyboard())
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

    await message.answer(
        f"✅ <b>Жавобингиз қабул қилинди!</b>\n\n"
        f"📋 <b>Топшириқ:</b> #{task_id} — {task.title}\n"
        f"💬 <b>Жавоб:</b> {response_text[:200]}{'...' if len(response_text) > 200 else ''}\n\n"
        f"Маълумот раҳбарга ва дашбордга узатилди.",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

    # Раҳбарга хабар
    try:
        ceo_chat_id = os.getenv("TELEGRAM_CEO_CHAT_ID") or "5950380558"

        deadline_str = "—"
        if task.deadline:
            dl = task.deadline
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc).astimezone(UZ_TZ)
            deadline_str = dl.strftime("%d.%m.%Y %H:%M")

        notify_text = (
            f"📩 <b>ХОДИМ ЖАВОБИ — Топшириқ #{task_id}</b>\n\n"
            f"👤 <b>Ижрочи:</b> {task.assigned_name}\n"
            f"📌 <b>Мавзу:</b> {task.title}\n"
            f"⏰ <b>Муддат:</b> {deadline_str}\n"
            f"💬 <b>Жавоб:</b>\n{response_text}\n"
        )
        await message.bot.send_message(chat_id=ceo_chat_id, text=notify_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Раҳбарга жавоб хабари юборишда хатолик: {e}")

    # Кузатувчига хабар
    if task.observer_telegram_id:
        try:
            observer_text = (
                f"📩 <b>НАЗОРАТ: Ходим жавоб юборди</b>\n\n"
                f"📋 <b>Топшириқ:</b> #{task_id} — {task.title}\n"
                f"👤 <b>Ижрочи:</b> {task.assigned_name}\n"
                f"💬 <b>Жавоб:</b>\n{response_text}\n"
            )
            await message.bot.send_message(
                chat_id=task.observer_telegram_id,
                text=observer_text,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Кузатувчига жавоб хабари юборишда хатолик: {e}")


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
