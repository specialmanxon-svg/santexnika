"""Telegram Bot Service for Employee GPS Attendance (Diyor Group).

Handles GPS location check-in and check-out using aiogram 3, validates
distance to store using Haversine formula (100m radius), and syncs
automatically with Diyor Group Dashboard.
"""
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
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import settings
from core.database import AsyncSessionLocal
from services.hr_service import hr_service, calculate_haversine_distance

logger = structlog.get_logger(__name__)

UZ_TZ = timezone(timedelta(hours=5))

# FSM States
class AttendanceStates(StatesGroup):
    waiting_for_location = State()

router = Router()

# Keyboards
def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Main employee menu keyboard."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🟢 Ишга келдим (GPS)"),
                KeyboardButton(text="🔴 Ишдан кетдим (GPS)")
            ],
            [
                KeyboardButton(text="📊 Менинг давомадим"),
                KeyboardButton(text="ℹ️ Дўкон ҳақида")
            ]
        ],
        resize_keyboard=True,
        input_field_placeholder="Буйруқни танланг..."
    )

def get_location_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard requesting live GPS location."""
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


# --- Handlers ---

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Start command: Greet employee and display attendance keyboard."""
    await state.clear()
    user_name = message.from_user.full_name or "Ҳурматли ходим"
    welcome_text = (
        f"👋 <b>Ассалому алайкум, {user_name}!</b>\n\n"
        f"🏢 <b>Diyor Group</b> — Ходимлар давоматини назорат қилиш ботига хуш келибсиз.\n\n"
        f"📍 <b>Қоида:</b> Ишга келиш ва кетишни қайд этиш учун сиз Бухоро марказий омбори "
        f"ёки савдо залидан <b>{int(settings.MAX_DISTANCE_METERS)} метр</b> радиус ичида бўлишингиз керак.\n\n"
        f"Иш кунини бошлаш учун <b>«🟢 Ишга келдим (GPS)»</b> тугмасини босинг."
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")


@router.message(F.text == "🟢 Ишга келдим (GPS)")
async def btn_checkin_clicked(message: types.Message, state: FSMContext):
    """Prompt employee to send live GPS coordinates for check-in."""
    await state.set_state(AttendanceStates.waiting_for_location)
    await state.update_data(action="CHECKIN")

    prompt_text = (
        f"📍 <b>Ишга келишни қайд этиш (GPS текширув)</b>\n\n"
        f"Илтимос, телефонингиз GPS тизими ёқилганлигига ишонч ҳосил қилинг ва "
        f"қуйидаги <b>«📍 Геолокацияни юбориш»</b> тугмасини босинг.\n\n"
        f"<i>(Чеклов: Дўкондан максимум {int(settings.MAX_DISTANCE_METERS)} метр масофа)</i>"
    )
    await message.answer(prompt_text, reply_markup=get_location_keyboard(), parse_mode="HTML")


@router.message(F.text == "🔴 Ишдан кетдим (GPS)")
async def btn_checkout_clicked(message: types.Message, state: FSMContext):
    """Prompt employee to send live GPS coordinates for check-out."""
    await state.set_state(AttendanceStates.waiting_for_location)
    await state.update_data(action="CHECKOUT")

    prompt_text = (
        f"🏁 <b>Иш сменасини якунлаш (GPS текширув)</b>\n\n"
        f"Ишдан кетишни қайд этиш ва бугун ишланган соатларни автоматик ҳисоблаш учун "
        f"қуйидаги <b>«📍 Геолокацияни юбориш»</b> тугмасини босинг."
    )
    await message.answer(prompt_text, reply_markup=get_location_keyboard(), parse_mode="HTML")


@router.message(F.text == "❌ Бекор қилиш")
async def btn_cancel(message: types.Message, state: FSMContext):
    """Cancel current operation and return to main menu."""
    await state.clear()
    await message.answer("❌ Амал бекор қилинди.", reply_markup=get_main_keyboard())


@router.message(F.text == "ℹ️ Дўкон ҳақида")
async def btn_store_info(message: types.Message):
    """Display store GPS info and working rules."""
    map_url = f"https://www.google.com/maps?q={settings.STORE_LAT},{settings.STORE_LON}"
    info_text = (
        f"🏢 <b>Diyor Group — Марказий дўкон ва омбор</b>\n\n"
        f"📍 <b>Шаҳар:</b> Бухоро\n"
        f"🌐 <b>Координаталар:</b> <code>{settings.STORE_LAT}, {settings.STORE_LON}</code>\n"
        f"📏 <b>Рухсат этилган радиус:</b> {int(settings.MAX_DISTANCE_METERS)} метр\n"
        f"⏰ <b>Иш бошланиш вақти:</b> {settings.store_work_start_hour}:00 (Бухоро вақти билан)\n\n"
        f"🗺 <a href='{map_url}'>Google Харитада дўконни кўриш</a>"
    )
    await message.answer(info_text, reply_markup=get_main_keyboard(), parse_mode="HTML", disable_web_page_preview=True)


@router.message(F.text == "📊 Менинг давомадим")
async def btn_my_attendance(message: types.Message):
    """Show personal attendance records for this employee."""
    emp_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        records = await hr_service.get_timesheets(session, employee_id=emp_id)

    if not records:
        await message.answer(
            "📋 <b>Сизнинг давомад маълумотларингиз ҳали топилмади.</b>\n"
            "Ишга келганингизда «🟢 Ишга келдим (GPS)» тугмасини босинг.",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        return

    lines = ["📋 <b>Сизнинг сўнгги давомад қайдларингиз:</b>\n"]
    for r in records[:7]:
        c_in = r.get("checkin_time", "—")
        c_out = r.get("checkout_time", "—")
        st = r.get("status", "—")
        hours = r.get("total_hours", "—")
        lines.append(f"📅 <b>Келди:</b> {c_in} | <b>Кетди:</b> {c_out}")
        lines.append(f"⏱ <b>Ишланди:</b> {hours} | <b>Ҳолат:</b> {st}\n")

    await message.answer("\n".join(lines), reply_markup=get_main_keyboard(), parse_mode="HTML")


@router.message(F.location)
async def handle_location(message: types.Message, state: FSMContext):
    """
    Handle live GPS coordinates sent by employee.
    Validates against all registered workplaces/sites using hr_service,
    and updates work_timesheets in database for real-time dashboard sync.
    """
    current_data = await state.get_data()
    action = current_data.get("action", "CHECKIN")
    await state.clear()

    lat = message.location.latitude
    lon = message.location.longitude

    employee_id = message.from_user.id
    employee_name = message.from_user.full_name or message.from_user.username or f"Ходим #{employee_id}"
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
                    device_info=f"Telegram Bot ({message.from_user.id})"
                )

            att_status = res.get("attendance_status", "Ўз вақтида (GPS тасдиқланди)")
            obj_name = res.get("object_name", "Асосий дўкон")
            dist_val = res.get("distance_meters", 0.0)
            status_icon = "✅" if "Ўз вақтида" in att_status else "⚠️"

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
                f"❌ <b>Сиз иш жойида эмассиз!</b>\n\n"
                f"⚠️ {str(ve)}\n\n"
                f"Ишга келишни қайд этиш учун объект ҳудудига келиб, қайта уриниб кўринг."
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
                        f"❌ <b>Сиз иш жойида эмассиз!</b>\n\n"
                        f"Ҳеч бир ишчи объект ҳудудида эмассиз (Энг яқин объект: {closest_loc['name']}, "
                        f"масофа: {int(min_d)} метр. Рухсат этилган: {int(closest_loc['radius_meters'])} метр).\n\n"
                        f"Ишдан кетишни қайд этиш учун объект ҳудудида туриб тугмани босинг."
                    )
                    await message.answer(err_msg, reply_markup=get_main_keyboard(), parse_mode="HTML")
                    return

                res = await hr_service.checkout(
                    session=session,
                    employee_id=employee_id
                )

            total_h = res.get("total_hours", 0.0)
            obj_title = matched_loc['name'] if matched_loc else "Дўкон/Объект"
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


async def run_bot_polling():
    """Run bot in standalone long-polling mode."""
    bot, dp = create_bot_and_dispatcher()
    logger.info("telegram_bot_polling_started", bot_token_prefix=settings.telegram_bot_token[:10])
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_bot_polling())
