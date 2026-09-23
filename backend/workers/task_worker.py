"""Background worker: Топшириқлар муддат назорати ва автоматик эслатмалар."""
import asyncio
import structlog
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, and_
from core.database import AsyncSessionLocal
from models.task import Task

logger = structlog.get_logger(__name__)

UZ_TZ = timezone(timedelta(hours=5))


async def _check_and_remind():
    """
    1. Муддатига 2 соат қолган топшириқларга эслатма юбориш.
    2. Муддати ўтган топшириқларни 'expired' қилиш.
    """
    now = datetime.utcnow()
    two_hours_later = now + timedelta(hours=2)

    async with AsyncSessionLocal() as session:
        # ── 1. Муддати ўтган топшириқлар → expired ──
        stmt_expired = select(Task).where(
            and_(
                Task.deadline != None,  # noqa: E711
                Task.deadline < now,
                Task.status.in_(["new", "in_progress"]),
            )
        )
        result = await session.execute(stmt_expired)
        expired_tasks = result.scalars().all()

        for task in expired_tasks:
            task.status = "expired"
            task.updated_at = datetime.utcnow()
            logger.info("task_expired", task_id=task.id, title=task.title)

            # Раҳбарга хабар
            try:
                from config import settings
                from aiogram import Bot
                import os

                bot = Bot(token=settings.telegram_bot_token)
                ceo_chat_id = os.getenv("TELEGRAM_CEO_CHAT_ID") or "5950380558"

                deadline_str = "—"
                if task.deadline:
                    dl = task.deadline
                    if dl.tzinfo is None:
                        dl = dl.replace(tzinfo=timezone.utc).astimezone(UZ_TZ)
                    deadline_str = dl.strftime("%d.%m.%Y %H:%M")

                text = (
                    f"⚠️ <b>ТОПШИРИҚ МУДДАТИ ЎТДИ!</b>\n\n"
                    f"📋 <b>#{task.id}:</b> {task.title}\n"
                    f"👤 <b>Ижрочи:</b> {task.assigned_name}\n"
                    f"⏰ <b>Муддат:</b> {deadline_str}\n"
                    f"📊 <b>Статус:</b> expired (муддати ўтган)\n"
                )
                await bot.send_message(chat_id=ceo_chat_id, text=text, parse_mode="HTML")
                await bot.session.close()
            except Exception as e:
                logger.error("expired_notification_failed", task_id=task.id, error=str(e))

        if expired_tasks:
            await session.commit()

        # ── 2. Муддатига 2 соат қолган топшириқлар → эслатма ──
        stmt_remind = select(Task).where(
            and_(
                Task.deadline != None,  # noqa: E711
                Task.deadline > now,
                Task.deadline <= two_hours_later,
                Task.status.in_(["new", "in_progress"]),
                Task.reminder_sent == 0,
            )
        )
        result2 = await session.execute(stmt_remind)
        upcoming_tasks = result2.scalars().all()

        for task in upcoming_tasks:
            if not task.assigned_telegram_id:
                continue

            try:
                from config import settings
                from aiogram import Bot
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

                bot = Bot(token=settings.telegram_bot_token)

                deadline_str = "—"
                if task.deadline:
                    dl = task.deadline
                    if dl.tzinfo is None:
                        dl = dl.replace(tzinfo=timezone.utc).astimezone(UZ_TZ)
                    deadline_str = dl.strftime("%d.%m.%Y %H:%M")

                text = (
                    f"🔔 <b>ЭСЛАТМА: Муддатига 2 соат қолди!</b>\n\n"
                    f"📋 <b>Топшириқ #{task.id}:</b> {task.title}\n"
                    f"⏰ <b>Муддат:</b> {deadline_str}\n\n"
                    f"Илтимос, топшириқни бажариб ҳисоботингизни юборинг!"
                )

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✍️ Жавоб юбориш",
                            callback_data=f"task_respond_{task.id}",
                        ),
                    ]
                ])

                await bot.send_message(
                    chat_id=task.assigned_telegram_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
                task.reminder_sent = 1
                task.updated_at = datetime.utcnow()
                logger.info("auto_reminder_sent", task_id=task.id, employee=task.assigned_name)
                await bot.session.close()
            except Exception as e:
                logger.error("auto_reminder_failed", task_id=task.id, error=str(e))

        if upcoming_tasks:
            await session.commit()


async def start_task_reminder_loop():
    """Ҳар 60 секундда топшириқ муддатларини текшириш."""
    logger.info("task_reminder_loop_started")
    await asyncio.sleep(10)  # Дастлабки кутиш
    while True:
        try:
            await _check_and_remind()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("task_reminder_loop_error", error=str(e))
        await asyncio.sleep(60)
