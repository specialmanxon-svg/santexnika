"""Background worker: Топшириқлар муддат назорати ва автоматик эслатмалар (APScheduler + Async Worker)."""
import os
import asyncio
import structlog
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, and_
from core.database import AsyncSessionLocal
from models.task import Task

logger = structlog.get_logger(__name__)

UZ_TZ = timezone(timedelta(hours=5))
_scheduler = None


async def _check_and_remind():
    """
    Топшириқларни автоматик текшириш ва эслатмалар юбориш:
    1-эслатма (Муддат тугашига 1 соат қолганда): Ижрочига эслатма хабари чиқади.
    2-эслатма (Муддат тугаб, вазифа бажарилмаган бўлса):
      - Статус автоматик тарзда 'expired' (Муддати ўтган) бўлади.
      - Ижрочи ва топшириқ берувчи раҳбарга огоҳлантириш юборилади.
    """
    now = datetime.utcnow()
    one_hour_later = now + timedelta(hours=1)

    async with AsyncSessionLocal() as session:
        # ── 1. МУДДАТИ ЎТГАН ТОПШИРИҚЛАР (Deadline <= now) ──
        stmt_expired = select(Task).where(
            and_(
                Task.deadline != None,  # noqa: E711
                Task.deadline <= now,
                Task.status.in_(["new", "in_progress"]),
            )
        )
        result_expired = await session.execute(stmt_expired)
        expired_tasks = result_expired.scalars().all()

        for task in expired_tasks:
            # Статусни автоматик 'expired' (Муддати ўтган) га айлантириш
            task.status = "expired"
            task.updated_at = datetime.utcnow()

            # Агар 2-эслатма ҳали юборилмаган бўлса: Ижрочи ва Раҳбарга юбориш
            if (task.reminder_sent or 0) < 2:
                task.reminder_sent = 2
                deadline_str = "—"
                if task.deadline:
                    dl = task.deadline
                    if dl.tzinfo is None:
                        dl = dl.replace(tzinfo=timezone.utc).astimezone(UZ_TZ)
                    deadline_str = dl.strftime("%d.%m.%Y %H:%M")

                try:
                    from config import settings
                    from aiogram import Bot
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

                    bot = Bot(token=settings.telegram_bot_token)

                    # А) Ижрочи ходимга хабар
                    if task.assigned_telegram_id:
                        text_employee = (
                            f"🚨 <b>Муддат ўтиб кетди!</b> «{task.title}» топшириғи муддатида бажарилмади! Ҳолати: Муддати ўтган\n\n"
                            f"📋 <b>#{task.id}:</b> {task.title}\n"
                            f"⏰ <b>Белгиланган муддат:</b> {deadline_str}\n"
                            f"📊 <b>Ҳолати:</b> 🔴 Муддати ўтган\n\n"
                            f"<i>Илтимос, зудлик билан топшириқни бажариб ҳисоботингизни юборинг!</i>"
                        )
                        kb_employee = InlineKeyboardMarkup(inline_keyboard=[
                            [
                                InlineKeyboardButton(text="✍️ Ҳисобот юбориш", callback_data=f"task_respond_{task.id}"),
                                InlineKeyboardButton(text="✅ Бажардим", callback_data=f"task_done_{task.id}"),
                            ]
                        ])
                        try:
                            await bot.send_message(
                                chat_id=task.assigned_telegram_id,
                                text=text_employee,
                                reply_markup=kb_employee,
                                parse_mode="HTML",
                            )
                            logger.info("expired_reminder_sent_to_employee", task_id=task.id, chat_id=task.assigned_telegram_id)
                        except Exception as ee:
                            logger.warning("expired_reminder_to_emp_failed", task_id=task.id, error=str(ee))

                    # Б) Топшириқ берувчи раҳбарга хабар
                    target_creator_chat = task.creator_chat_id or os.getenv("TELEGRAM_CEO_CHAT_ID") or "5950380558"
                    if target_creator_chat:
                        text_creator = (
                            f"🚨 <b>Муддат ўтиб кетди!</b> «{task.title}» топшириғи муддатида бажарилмади! Ҳолати: Муддати ўтган\n\n"
                            f"📋 <b>#{task.id}:</b> {task.title}\n"
                            f"👤 <b>Ижрочи:</b> {task.assigned_name}\n"
                            f"⏰ <b>Муддат:</b> {deadline_str}\n"
                            f"📊 <b>Ҳолати:</b> 🔴 Муддати ўтган (expired)\n\n"
                            f"🌐 <a href='https://santexnika.onrender.com/dashboard#tasks'>Дашбордда кўриш</a>"
                        )
                        try:
                            await bot.send_message(
                                chat_id=target_creator_chat,
                                text=text_creator,
                                parse_mode="HTML",
                            )
                            logger.info("expired_reminder_sent_to_creator", task_id=task.id, chat_id=target_creator_chat)
                        except Exception as ce:
                            logger.warning("expired_reminder_to_creator_failed", task_id=task.id, error=str(ce))

                    await bot.session.close()
                except Exception as ex:
                    logger.error("expired_notification_failed", task_id=task.id, error=str(ex))

        if expired_tasks:
            await session.commit()

        # ── 2. МУДДАТИГА 1 СОАТ ҚОЛГАН ТОПШИРИҚЛАР (1-эслатма) ──
        stmt_remind_1h = select(Task).where(
            and_(
                Task.deadline != None,  # noqa: E711
                Task.deadline > now,
                Task.deadline <= one_hour_later,
                Task.status.in_(["new", "in_progress"]),
                Task.reminder_sent == 0,
            )
        )
        result_remind_1h = await session.execute(stmt_remind_1h)
        upcoming_tasks = result_remind_1h.scalars().all()

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
                    f"⏳ <b>Эслатма:</b> «{task.title}» топшириғини топшириш муддатига 1 соат қолди! Вақт: {deadline_str}\n\n"
                    f"📋 <b>#{task.id}:</b> {task.title}\n"
                    f"📝 <b>Тафсилот:</b> {task.description or '—'}\n\n"
                    f"<i>Илтимос, вазифани ўз вақтида якунлаб ҳисоботингизни юборинг!</i>"
                )

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✍️ Жавоб юбориш",
                            callback_data=f"task_respond_{task.id}",
                        ),
                        InlineKeyboardButton(
                            text="✅ Бажардим",
                            callback_data=f"task_done_{task.id}",
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
                logger.info("auto_reminder_1h_sent", task_id=task.id, employee=task.assigned_name)
                await bot.session.close()
            except Exception as e:
                logger.error("auto_reminder_1h_failed", task_id=task.id, error=str(e))

        if upcoming_tasks:
            await session.commit()


def init_task_scheduler():
    """
    APScheduler орқали ҳар 5 дақиқада очиқ вазифаларни текшириш ва эслатма юборишни бошлаш.
    """
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        if _scheduler is None or not _scheduler.running:
            _scheduler = AsyncIOScheduler(timezone=UZ_TZ)
            _scheduler.add_job(
                _check_and_remind,
                trigger="interval",
                minutes=5,
                id="task_deadline_reminder_job",
                replace_existing=True,
            )
            _scheduler.start()
            logger.info("apscheduler_task_reminder_started", interval="5_minutes")
        return _scheduler
    except Exception as e:
        logger.warning("apscheduler_init_failed", error=str(e))
        return None


async def start_task_reminder_loop():
    """Фонда (asyncio loop) ҳар 60 сонияда топшириқ муддатларини узлуксиз текшириб туриш."""
    logger.info("task_reminder_loop_started")
    # APScheduler ҳам параллел ишга туширилади
    init_task_scheduler()

    await asyncio.sleep(10)  # Дастлабки кутиш
    while True:
        try:
            await _check_and_remind()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("task_reminder_loop_error", error=str(e))
        await asyncio.sleep(60)


def shutdown_task_scheduler():
    """APScheduler ни тўхтатиш."""
    global _scheduler
    if _scheduler and _scheduler.running:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("apscheduler_task_reminder_stopped")
        except Exception:
            pass

