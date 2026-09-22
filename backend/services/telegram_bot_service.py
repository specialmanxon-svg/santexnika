"""Telegram Bot Service for Employee GPS Attendance (Diyor Group).

Features:
1. Secure Phone Number Authorization (/start + request_contact=True).
   Matches contact against MoySklad employees and authorized_employees table.
2. Two-button persistent main menu for authorized employees:
   - [ 🟢 Ишга келдим (GPS) ]
   - [ 🔴 Ишдан кетдим (GPS) ]
3. Multi-workplace/geofence GPS location validation (Haversine formula).
4. Direct real-time synchronization with Diyor Group Dashboard.
"""
import re
import math
import asyncio
import structlog
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher, F, Router, types
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
from core.database import AsyncSessionLocal
from models.hr import AuthorizedEmployee
from services.hr_service import hr_service, calculate_haversine_distance
from services.moysklad_client import MoySkladClient

logger = structlog.get_logger(__name__)

UZ_TZ = timezone(timedelta(hours=5))


def normalize_phone_digits(raw: str) -> str:
    """Normalize phone number to numeric digits e.g. 998901234567."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 9:
        digits = "998" + digits
    elif len(digits) == 10 and digits.startswith("8"):
        digits = "998" + digits[1:]
    return digits


async def sync_to_backend_api(payload: dict):
    """Ходимнинг ботдан юборган давомадини тўғридан-тўғри Backend API га узатиш."""
    import os
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
                        logger.info("attendance_synced_to_backend", base=base, status=res.status_code)
                        break
            except Exception as ex:
                logger.debug("backend_sync_failed", base=base, error=str(ex))
    except Exception as e:
        logger.warning("sync_to_backend_api_error", error=str(e))


async def notify_management(bot: Bot, text: str):
    """Раҳбарият гуруҳи ва каналига хабар юбориш."""
    import os
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
            logger.info("notified_management", chat_id=cid)
        except Exception as ex:
            logger.warning("notify_management_error", chat_id=cid, error=str(ex))


# --- FSM States ---
class AttendanceStates(StatesGroup):
    waiting_for_location = State()


router = Router()


# --- Keyboards ---
def get_auth_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard prompting employee to share phone contact for authorization."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Телефон рақамни юбориш", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="«Телефон рақамни юбориш» тугмасини босинг..."
    )


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Persistent 2-button menu for verified employees."""
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
    """Keyboard requesting live GPS location."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Ҳозирги геолокацияни юбориш", request_location=True)],
            [KeyboardButton(text="❌ Бекор қилиш")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


# --- Helper Database Functions ---
async def get_authorized_employee(tg_id: int) -> Optional[AuthorizedEmployee]:
    """Retrieve active authorized employee by Telegram ID."""
    async with AsyncSessionLocal() as session:
        stmt = select(AuthorizedEmployee).where(
            AuthorizedEmployee.telegram_id == tg_id,
            AuthorizedEmployee.is_active == 1
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()


async def find_employee_in_moysklad_or_db(digits: str) -> Optional[Dict[str, Any]]:
    """
    Search for employee by last 9 digits of phone number in MoySklad and local DB.
    """
    if len(digits) < 9:
        return None
    phone_9 = digits[-9:]

    # 1. Check local authorized_employees table
    async with AsyncSessionLocal() as session:
        stmt = select(AuthorizedEmployee).where(AuthorizedEmployee.is_active == 1)
        res = await session.execute(stmt)
        for emp in res.scalars().all():
            emp_d = normalize_phone_digits(emp.phone_number)
            if emp_d and emp_d[-9:] == phone_9:
                return {
                    "id": emp.id,
                    "name": emp.employee_name,
                    "phone": emp.phone_number,
                    "moysklad_id": emp.moysklad_id,
                    "source": "local_db"
                }

    # 2. Check MoySklad API
    try:
        ms_client = MoySkladClient()
        resp = await ms_client._request("GET", "/entity/employee", params={"limit": 100})
        rows = resp.get("rows", [])
        for r in rows:
            if not r.get("archived", False):
                ms_phone = r.get("phone")
                if ms_phone:
                    emp_d = normalize_phone_digits(ms_phone)
                    if emp_d and emp_d[-9:] == phone_9:
                        return {
                            "name": r.get("name", "Ходим"),
                            "phone": ms_phone,
                            "moysklad_id": r.get("id"),
                            "source": "moysklad"
                        }
    except Exception as e:
        logger.warning("moysklad_employee_lookup_failed", error=str(e))

    return None


# --- Bot Handlers ---

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """
    Handle /start:
    - If user is already authorized: greet and show 2 main GPS buttons.
    - If user is not authorized: request phone number via request_contact=True.
    """
    await state.clear()
    emp = await get_authorized_employee(message.from_user.id)

    if emp:
        welcome_text = (
            f"👋 <b>Ассалому алайкум, {emp.employee_name}!</b>\n\n"
            f"🏢 <b>Diyor Group</b> — Сиз тизимда муваффақиятли тасдиқлангансиз.\n\n"
            f"Ишга келиш ва кетишингизни қайд этиш учун қуйидаги тугмалардан фойдаланинг:"
        )
        await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    else:
        auth_prompt = (
            f"👋 <b>Ассалому алайкум!</b>\n\n"
            f"🏢 <b>Diyor Group</b> — Ходимлар давоматини назорат қилиш ботига хуш келибсиз.\n\n"
            f"⚠️ <b>Хавфсизлик текшируви:</b>\n"
            f"Ботдан фақат компаниянинг рўйхатдан ўтган ходимлари фойдаланиши мумкин. "
            f"Шахсингизни тасдиқлаш учун илтимос, пастдаги <b>«📱 Телефон рақамни юбориш»</b> тугмасини босинг."
        )
        await message.answer(auth_prompt, reply_markup=get_auth_keyboard(), parse_mode="HTML")


@router.message(F.contact)
async def handle_contact(message: types.Message):
    """
    Handle shared phone contact:
    - Verifies contact belongs to the user.
    - Matches phone number with MoySklad or local database.
    - Links telegram_id to employee.
    """
    contact = message.contact

    # Security check: ensure user didn't forward someone else's contact
    if contact.user_id and contact.user_id != message.from_user.id:
        await message.answer(
            "❌ <b>Хатолик:</b> Илтимос, фақат ўзингизнинг телефон рақамингизни юборинг!",
            reply_markup=get_auth_keyboard(),
            parse_mode="HTML"
        )
        return

    raw_phone = contact.phone_number
    digits = normalize_phone_digits(raw_phone)

    # Match with employees
    match = await find_employee_in_moysklad_or_db(digits)

    if not match:
        logger.warning("unauthorized_contact_attempt", phone=digits, user=message.from_user.full_name)
        reject_text = (
            f"❌ <b>Сиз ходимлар рўйхатида топилмадингиз!</b>\n\n"
            f"📱 Сизнинг рақамингиз: <code>+{digits}</code>\n\n"
            f"⚠️ Ушбу телефон рақами компания ходимлари базасида рўйхатга олинмаган. "
            f"Агар сиз Diyor Group ходими бўлсангиз, илтимос маъмуриятга ёки раҳбарингизга "
            f"мурожаат қилиб рақамингизни базага киритинг ва қайта уриниб кўринг."
        )
        await message.answer(reject_text, reply_markup=get_auth_keyboard(), parse_mode="HTML")
        return

    # Link/bind telegram_id to employee in authorized_employees
    emp_name = match["name"]
    moysklad_id = match.get("moysklad_id")

    async with AsyncSessionLocal() as session:
        # Check if record already exists for this phone or tg_id
        stmt = select(AuthorizedEmployee).where(
            (AuthorizedEmployee.telegram_id == message.from_user.id) |
            (AuthorizedEmployee.phone_number == f"+{digits}")
        )
        res = await session.execute(stmt)
        emp_record = res.scalar_one_or_none()

        if emp_record:
            emp_record.employee_name = emp_name
            emp_record.phone_number = f"+{digits}"
            emp_record.telegram_id = message.from_user.id
            emp_record.telegram_username = message.from_user.username
            emp_record.moysklad_id = moysklad_id
            emp_record.is_active = 1
            emp_record.authorized_at = datetime.utcnow()
        else:
            emp_record = AuthorizedEmployee(
                employee_name=emp_name,
                phone_number=f"+{digits}",
                telegram_id=message.from_user.id,
                telegram_username=message.from_user.username,
                moysklad_id=moysklad_id,
                role="Ходим",
                is_active=1,
                authorized_at=datetime.utcnow()
            )
            session.add(emp_record)

        await session.commit()

    logger.info("employee_telegram_authorized", user=emp_name, phone=digits, tg_id=message.from_user.id)

    approved_text = (
        f"✅ <b>Тасдиқланди: {emp_name}!</b>\n\n"
        f"Сиз Diyor Group ходимлари рўйхатидан муваффақиятли ўтдингиз.\n"
        f"Энди ишга келиш ва кетишингизни GPS орқали белгилашингиз мумкин. 💼"
    )
    await message.answer(approved_text, reply_markup=get_main_keyboard(), parse_mode="HTML")


@router.message(F.text.contains("Ишга келдим") | (F.text == "🟢 Ишга келдим (GPS)"))
async def btn_checkin_clicked(message: types.Message, state: FSMContext):
    """Prompt verified employee to send live GPS coordinates for check-in."""
    emp = await get_authorized_employee(message.from_user.id)
    if not emp:
        await message.answer(
            "⚠️ <b>Сиз ҳали авторизациядан ўтмагансиз!</b>\n"
            "Илтимос, аввал телефон рақамингизни юбориб, шахсингизни тасдиқланг:",
            reply_markup=get_auth_keyboard(),
            parse_mode="HTML"
        )
        return

    await state.set_state(AttendanceStates.waiting_for_location)
    await state.update_data(action="CHECKIN", employee_id=emp.id, employee_name=emp.employee_name)

    prompt_text = (
        f"Ҳурматли <b>{emp.employee_name}</b>!\n"
        f"Илтимос, пастдаги «📍 Ҳозирги геолокацияни юбориш» тугмасини босинг:"
    )
    await message.answer(prompt_text, reply_markup=get_location_keyboard(), parse_mode="HTML")


@router.message(F.text.contains("Ишдан кетдим") | (F.text == "🔴 Ишдан кетдим (GPS)"))
async def btn_checkout_clicked(message: types.Message, state: FSMContext):
    """Prompt verified employee to send live GPS coordinates for check-out."""
    emp = await get_authorized_employee(message.from_user.id)
    if not emp:
        await message.answer(
            "⚠️ <b>Сиз ҳали авторизациядан ўтмагансиз!</b>\n"
            "Илтимос, аввал телефон рақамингизни юбориб, шахсингизни тасдиқланг:",
            reply_markup=get_auth_keyboard(),
            parse_mode="HTML"
        )
        return

    await state.set_state(AttendanceStates.waiting_for_location)
    await state.update_data(action="CHECKOUT", employee_id=emp.id, employee_name=emp.employee_name)

    prompt_text = (
        f"Ҳурматли <b>{emp.employee_name}</b>!\n"
        f"Илтимос, пастдаги «📍 Ҳозирги геолокацияни юбориш» тугмасини босинг:"
    )
    await message.answer(prompt_text, reply_markup=get_location_keyboard(), parse_mode="HTML")


@router.message(F.text.contains("Бекор қилиш") | (F.text == "❌ Бекор қилиш"))
async def btn_cancel(message: types.Message, state: FSMContext):
    """Cancel current operation and return to main menu."""
    await state.clear()
    emp = await get_authorized_employee(message.from_user.id)
    kb = get_main_keyboard() if emp else get_auth_keyboard()
    await message.answer("❌ Амал бекор қилинди.", reply_markup=kb)


@router.message(F.location)
async def handle_location(message: types.Message, state: FSMContext):
    """
    Handle live GPS coordinates sent by verified employee.
    Validates against all registered workplaces/sites using hr_service,
    and updates work_timesheets in database for real-time dashboard sync.
    """
    emp = await get_authorized_employee(message.from_user.id)
    if not emp:
        await state.clear()
        await message.answer(
            "⚠️ <b>Сиз ҳали авторизациядан ўтмагансиз!</b>\n"
            "Илтимос, аввал телефон рақамингизни юбориб шахсингизни тасдиқланг.",
            reply_markup=get_auth_keyboard(),
            parse_mode="HTML"
        )
        return

    current_data = await state.get_data()
    action = current_data.get("action", "CHECKIN")
    await state.clear()

    lat = message.location.latitude
    lon = message.location.longitude

    employee_id = emp.id
    employee_name = emp.employee_name
    now_local = datetime.now(UZ_TZ)
    time_str = now_local.strftime("%d.%m.%Y %H:%M")

    # --- 1. ACTION: CHECK-IN (Ишга келдим) ---
    if action == "CHECKIN":
        try:
            async with AsyncSessionLocal() as session:
                res = await hr_service.checkin(
                    session=session,
                    employee_id=employee_id,
                    employee_name=employee_name,
                    latitude=lat,
                    longitude=lon,
                    device_info="📱 Telegram"
                )

            att_status = res.get("attendance_status", "Ўз вақтида (GPS тасдиқланди)")
            obj_name = res.get("object_name", "Марказий дўкон (Бухоро)")
            dist_val = res.get("distance_meters", 0.0)
            status_icon = "✅" if "Ўз вақтида" in att_status else "⚠️"

            # Backend API га синхронизация
            payload = {
                "employee_id": employee_id,
                "employee_name": employee_name,
                "action": "check_in",
                "latitude": lat,
                "longitude": lon,
                "source": "Telegram",
                "device_info": "📱 Telegram",
                "object_name": obj_name,
                "distance_meters": dist_val,
                "timestamp": datetime.now().isoformat()
            }
            asyncio.create_task(sync_to_backend_api(payload))

            # Раҳбариятга билдиришнома
            mgmt_text = (
                f"📍 <b>Янги давомад қайди (GPS)</b>\n\n"
                f"👤 <b>Ходим:</b> {employee_name}\n"
                f"🏢 <b>Объект:</b> {obj_name}\n"
                f"⏰ <b>Вақти:</b> {time_str}\n"
                f"📏 <b>Масофа:</b> {dist_val} метр\n"
                f"📊 <b>Ҳолат:</b> {status_icon} {att_status}\n"
                f"📱 <b>Манба:</b> 📱 Telegram\n\n"
                f"🌐 <a href='https://diyorgroup.uz/index.html#hr'>Дашбордда кўриш</a>"
            )
            asyncio.create_task(notify_management(message.bot, mgmt_text))

            success_msg = (
                f"✅ <b>Ишга келиш муваффақиятли қайд этилди!</b>\n\n"
                f"👤 <b>Ходим:</b> {employee_name}\n"
                f"🏢 <b>Объект:</b> <b>{obj_name}</b>\n"
                f"⏰ <b>Келган вақти:</b> {time_str}\n"
                f"📏 <b>Масофа:</b> {dist_val} метр\n"
                f"📊 <b>Ҳолат:</b> {status_icon} {att_status}\n\n"
                f"Давомад маълумотингиз бошқарув панели (Dashboard) га узатилди. "
                f"Бардам бўлинг, кунингиз хайрли ва баракали ўтсин! 💼"
            )
            await message.answer(success_msg, reply_markup=get_main_keyboard(), parse_mode="HTML")

        except ValueError as ve:
            logger.warning("telegram_checkin_rejected", user=employee_name, reason=str(ve))
            err_msg = (
                f"❌ <b>Ишга келиш қайд этилмади!</b>\n\n"
                f"Сиз иш жойида эмассиз.\n"
                f"⚠️ {str(ve)}\n\n"
                f"<i>Илтимос, дўкон ёки объект ҳудудига яқин келиб қайта уриниб кўринг!</i>"
            )
            await message.answer(err_msg, reply_markup=get_main_keyboard(), parse_mode="HTML")
        except Exception as e:
            logger.error("telegram_checkin_db_error", error=str(e))
            await message.answer(f"❌ Хатолик юз берди: {str(e)}", reply_markup=get_main_keyboard())

    # --- 2. ACTION: CHECK-OUT (Ишдан кетдим) ---
    elif action == "CHECKOUT":
        try:
            async with AsyncSessionLocal() as session:
                # Validate proximity to any active workplace
                locations = await hr_service.get_locations(session, active_only=True)
                matched_loc = None
                closest_loc = None
                min_d = float('inf')
                for loc in locations:
                    d = calculate_haversine_distance(lat, lon, loc["latitude"], loc["longitude"])
                    if d < min_d:
                        min_d = d
                        closest_loc = loc
                    if d <= loc["radius_meters"]:
                        matched_loc = loc
                        break

                if not matched_loc and closest_loc:
                    err_msg = (
                        f"❌ <b>Ишдан кетиш қайд этилмади!</b>\n\n"
                        f"Сиз иш жойида эмассиз.\n"
                        f"🏢 <b>Энг яқин объект:</b> {closest_loc['name']}\n"
                        f"📏 <b>Масофа:</b> {int(min_d)} метр\n"
                        f"⭕️ <b>Рухсат этилган радиус:</b> {int(closest_loc['radius_meters'])} метр\n\n"
                        f"<i>Илтимос, ишдан кетишни қайд этиш учун объект ҳудудида туриб тугмани босинг!</i>"
                    )
                    await message.answer(err_msg, reply_markup=get_main_keyboard(), parse_mode="HTML")
                    return

                res = await hr_service.checkout(
                    session=session,
                    employee_id=employee_id,
                    device_info="📱 Telegram"
                )

            total_h = res.get("total_hours", 0.0)
            obj_title = matched_loc['name'] if matched_loc else "Марказий дўкон (Бухоро)"

            # Backend API га синхронизация
            payload = {
                "employee_id": employee_id,
                "employee_name": employee_name,
                "action": "check_out",
                "latitude": lat,
                "longitude": lon,
                "source": "Telegram",
                "device_info": "📱 Telegram",
                "object_name": obj_title,
                "distance_meters": round(min_d, 1) if min_d != float('inf') else 0.0,
                "timestamp": datetime.now().isoformat()
            }
            asyncio.create_task(sync_to_backend_api(payload))

            # Раҳбариятга билдиришнома
            mgmt_text = (
                f"🏁 <b>Иш сменаси якунланди (GPS)</b>\n\n"
                f"👤 <b>Ходим:</b> {employee_name}\n"
                f"🏢 <b>Объект:</b> {obj_title}\n"
                f"⏰ <b>Вақти:</b> {time_str}\n"
                f"⏱ <b>Ишланган вақт:</b> {total_h:.2f} соат\n"
                f"📱 <b>Манба:</b> 📱 Telegram\n\n"
                f"🌐 <a href='https://diyorgroup.uz/index.html#hr'>Дашбордда кўриш</a>"
            )
            asyncio.create_task(notify_management(message.bot, mgmt_text))

            checkout_msg = (
                f"🏁 <b>Иш сменаси якунланди!</b>\n\n"
                f"👤 <b>Ходим:</b> {employee_name}\n"
                f"🏢 <b>Объект:</b> {obj_title}\n"
                f"⏰ <b>Кетган вақти:</b> {time_str}\n"
                f"⏱ <b>Бугун ишланган вақт:</b> {total_h:.2f} соат\n\n"
                f"Маълумотлар тизимда сақланди. Ҳорманг, яхши дам олинг! 🏠"
            )
            await message.answer(checkout_msg, reply_markup=get_main_keyboard(), parse_mode="HTML")

        except ValueError as ve:
            await message.answer(f"⚠️ {str(ve)}", reply_markup=get_main_keyboard())
        except Exception as e:
            logger.error("telegram_checkout_db_error", error=str(e))
            await message.answer(f"❌ Хатолик юз берди: {str(e)}", reply_markup=get_main_keyboard())


# --- Bot Lifecycle & Dispatcher ---
def create_bot_and_dispatcher():
    """Create and configure aiogram Bot and Dispatcher instances."""
    token = settings.telegram_bot_token
    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return bot, dp
