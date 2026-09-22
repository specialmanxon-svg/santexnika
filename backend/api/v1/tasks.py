"""Топшириқлар ва Назорат — Tasks CRUD API."""
import os
import time
import uuid
from pathlib import Path
import structlog
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Query, Form, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select, desc

from core.database import AsyncSessionLocal
from models.task import Task
from models.hr import AuthorizedEmployee

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/tasks", tags=["Топшириқлар ва Назорат"])

UZ_TZ = timezone(timedelta(hours=5))


# ── Pydantic Schemas ────────────────────────────────────────────

class TaskCreate(BaseModel):
    title: str = Field(..., description="Топшириқ мавзуси")
    description: Optional[str] = Field(None, description="Батафсил матн/сўров")
    assigned_to: int = Field(..., description="Ижрочи ходим ID")
    assigned_name: str = Field(..., description="Ижрочи ходим исми")
    observer_id: Optional[int] = Field(None, description="Кузатувчи ходим ID")
    observer_name: Optional[str] = Field(None, description="Кузатувчи ходим исми")
    deadline: Optional[str] = Field(None, description="Муддат ISO формат (YYYY-MM-DDTHH:MM)")
    voice_url: Optional[str] = Field(None, description="Овозли хабар URL")


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assigned_to: Optional[int] = None
    assigned_name: Optional[str] = None
    observer_id: Optional[int] = None
    observer_name: Optional[str] = None
    deadline: Optional[str] = None
    status: Optional[str] = None
    voice_url: Optional[str] = None


class TaskResponse(BaseModel):
    text: str = Field(..., description="Ходим жавоби / ҳисоботи")


class TaskStatusUpdate(BaseModel):
    status: str = Field(..., description="Янги статус: new, in_progress, completed, expired")


class TaskOut(BaseModel):
    id: int
    title: str
    description: Optional[str]
    assigned_to: int
    assigned_name: str
    assigned_telegram_id: Optional[int] = None
    observer_id: Optional[int]
    observer_name: Optional[str]
    observer_telegram_id: Optional[int] = None
    deadline: Optional[str]
    status: str
    employee_response: Optional[str]
    voice_url: Optional[str] = None
    reminder_sent: int = 0
    created_at: Optional[str]
    updated_at: Optional[str]


# ── Helper: Telegram хабар юбориш ───────────────────────────────

async def _send_task_notification_to_employee(task: Task):
    """Ижрочига Telegram орқали топшириқ (овозли ёки матнли) хабарини юбориш."""
    if not task.assigned_telegram_id:
        logger.warning("task_notify_skip_no_tg_id", task_id=task.id, employee=task.assigned_name)
        return

    try:
        from services.telegram_bot_service import create_bot_and_dispatcher
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

        bot, _ = create_bot_and_dispatcher()

        deadline_str = "—"
        if task.deadline:
            dl = task.deadline
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc).astimezone(UZ_TZ)
            deadline_str = dl.strftime("%d.%m.%Y %H:%M")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🟢 Қабул қилдим", callback_data=f"task_accept_{task.id}"),
                InlineKeyboardButton(text="✍️ Жавоб юбориш", callback_data=f"task_respond_{task.id}"),
            ]
        ])

        voice_sent = False
        if task.voice_url:
            voice_disk_path = Path(__file__).resolve().parent.parent.parent / task.voice_url.lstrip("/")
            if voice_disk_path.exists():
                caption = (
                    f"🎙 <b>ЯНГИ ОВОЗЛИ ТОПШИРИҚ #{task.id}</b>\n\n"
                    f"📌 <b>Мавзу:</b> {task.title}\n"
                    f"📝 <b>Тафсилот:</b> {task.description or '—'}\n"
                    f"⏰ <b>Муддат:</b> {deadline_str}\n"
                )
                try:
                    await bot.send_voice(
                        chat_id=task.assigned_telegram_id,
                        voice=FSInputFile(str(voice_disk_path)),
                        caption=caption,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
                    voice_sent = True
                    logger.info("task_voice_notification_sent", task_id=task.id, chat_id=task.assigned_telegram_id)
                except Exception as ve:
                    logger.warning("send_voice_failed_fallback_to_text", error=str(ve))

        if not voice_sent:
            text = (
                f"📋 <b>ЯНГИ ТОПШИРИҚ #{task.id}</b>\n\n"
                f"📌 <b>Мавзу:</b> {task.title}\n"
                f"📝 <b>Тафсилот:</b> {task.description or '—'}\n"
                f"⏰ <b>Муддат:</b> {deadline_str}\n"
            )
            await bot.send_message(
                chat_id=task.assigned_telegram_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            logger.info("task_text_notification_sent", task_id=task.id, chat_id=task.assigned_telegram_id)

        await bot.session.close()
    except Exception as e:
        logger.error("task_notification_failed", task_id=task.id, error=str(e))


async def _send_observer_notification(task: Task):
    """Кузатувчига маълумот учун овозли ёки матнли хабар юбориш."""
    if not task.observer_telegram_id:
        return

    try:
        from services.telegram_bot_service import create_bot_and_dispatcher
        from aiogram.types import FSInputFile
        bot, _ = create_bot_and_dispatcher()

        deadline_str = "—"
        if task.deadline:
            dl = task.deadline
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc).astimezone(UZ_TZ)
            deadline_str = dl.strftime("%d.%m.%Y %H:%M")

        voice_sent = False
        if task.voice_url:
            voice_disk_path = Path(__file__).resolve().parent.parent.parent / task.voice_url.lstrip("/")
            if voice_disk_path.exists():
                caption = (
                    f"👁 <b>НАЗОРАТ: Овозли топшириқ берилди</b>\n\n"
                    f"📋 <b>Топшириқ:</b> #{task.id}\n"
                    f"👤 <b>Ижрочи:</b> {task.assigned_name}\n"
                    f"📌 <b>Мавзу:</b> {task.title}\n"
                    f"⏰ <b>Муддат:</b> {deadline_str}\n"
                )
                try:
                    await bot.send_voice(
                        chat_id=task.observer_telegram_id,
                        voice=FSInputFile(str(voice_disk_path)),
                        caption=caption,
                        parse_mode="HTML",
                    )
                    voice_sent = True
                    logger.info("observer_voice_notification_sent", task_id=task.id, chat_id=task.observer_telegram_id)
                except Exception as ve:
                    logger.warning("observer_send_voice_failed", error=str(ve))

        if not voice_sent:
            text = (
                f"👁 <b>НАЗОРАТ: Ходимга топшириқ берилди</b>\n\n"
                f"📋 <b>Топшириқ:</b> #{task.id}\n"
                f"👤 <b>Ижрочи:</b> {task.assigned_name}\n"
                f"📌 <b>Мавзу:</b> {task.title}\n"
                f"⏰ <b>Муддат:</b> {deadline_str}\n"
            )
            await bot.send_message(
                chat_id=task.observer_telegram_id,
                text=text,
                parse_mode="HTML",
            )
            logger.info("observer_notification_sent", task_id=task.id, chat_id=task.observer_telegram_id)

        await bot.session.close()
    except Exception as e:
        logger.error("observer_notification_failed", task_id=task.id, error=str(e))


async def _send_reminder_to_employee(task: Task, urgent: bool = False):
    """Ходимга эслатма юбориш."""
    if not task.assigned_telegram_id:
        return

    try:
        from services.telegram_bot_service import create_bot_and_dispatcher
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        bot, _ = create_bot_and_dispatcher()

        deadline_str = "—"
        if task.deadline:
            dl = task.deadline
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc).astimezone(UZ_TZ)
            deadline_str = dl.strftime("%d.%m.%Y %H:%M")

        prefix = "🚨 <b>ШОШИЛИНЧ ЭСЛАТМА!</b>" if urgent else "🔔 <b>ЭСЛАТМА</b>"
        text = (
            f"{prefix}\n\n"
            f"📋 <b>Топшириқ #{task.id}:</b> {task.title}\n"
            f"⏰ <b>Муддат:</b> {deadline_str}\n"
            f"📊 <b>Статус:</b> {task.status}\n\n"
            f"Илтимос, топшириқни бажариб ҳисоботингизни юборинг!"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✍️ Жавоб юбориш", callback_data=f"task_respond_{task.id}"),
            ]
        ])

        await bot.send_message(
            chat_id=task.assigned_telegram_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        logger.info("task_reminder_sent", task_id=task.id, urgent=urgent)
        await bot.session.close()
    except Exception as e:
        logger.error("task_reminder_failed", task_id=task.id, error=str(e))


def _task_to_out(t: Task) -> TaskOut:
    """Task ORM → TaskOut Pydantic."""
    return TaskOut(
        id=t.id,
        title=t.title,
        description=t.description,
        assigned_to=t.assigned_to,
        assigned_name=t.assigned_name,
        assigned_telegram_id=t.assigned_telegram_id,
        observer_id=t.observer_id,
        observer_name=t.observer_name,
        observer_telegram_id=t.observer_telegram_id,
        deadline=t.deadline.isoformat() if t.deadline else None,
        status=t.status,
        employee_response=t.employee_response,
        voice_url=t.voice_url,
        reminder_sent=t.reminder_sent or 0,
        created_at=t.created_at.isoformat() if t.created_at else None,
        updated_at=t.updated_at.isoformat() if t.updated_at else None,
    )


# ── Endpoints ───────────────────────────────────────────────────

@router.get("/", response_model=List[TaskOut])
@router.get("", response_model=List[TaskOut])
async def list_tasks(
    status: Optional[str] = Query(None, description="Статус фильтри: new, in_progress, completed, expired"),
):
    """Барча топшириқлар рўйхати (статус бўйича фильтр)."""
    async with AsyncSessionLocal() as session:
        q = select(Task).order_by(desc(Task.created_at))
        if status:
            q = q.where(Task.status == status)
        result = await session.execute(q)
        tasks = result.scalars().all()
        return [_task_to_out(t) for t in tasks]


@router.post("/", response_model=TaskOut, status_code=201)
@router.post("", response_model=TaskOut, status_code=201)
async def create_task(data: TaskCreate):
    """Янги топшириқ яратиш ва Telegram орқали хабар юбориш."""
    async with AsyncSessionLocal() as session:
        # Ижрочи ходимнинг telegram_id сини олиш
        assigned_tg_id = None
        observer_tg_id = None

        emp = await session.get(AuthorizedEmployee, data.assigned_to)
        if emp:
            assigned_tg_id = emp.telegram_id

        if data.observer_id:
            obs = await session.get(AuthorizedEmployee, data.observer_id)
            if obs:
                observer_tg_id = obs.telegram_id

        # Дедлайнни парсинг қилиш
        deadline_dt = None
        if data.deadline:
            try:
                deadline_dt = datetime.fromisoformat(data.deadline)
            except ValueError:
                raise HTTPException(status_code=400, detail="Нотўғри муддат формати. ISO формат керак: YYYY-MM-DDTHH:MM")

        task = Task(
            title=data.title,
            description=data.description,
            assigned_to=data.assigned_to,
            assigned_name=data.assigned_name,
            assigned_telegram_id=assigned_tg_id,
            observer_id=data.observer_id,
            observer_name=data.observer_name,
            observer_telegram_id=observer_tg_id,
            deadline=deadline_dt,
            status="new",
            voice_url=data.voice_url,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        logger.info("task_created", task_id=task.id, assigned_to=task.assigned_name)

        # Telegram хабарлар (фон)
        import asyncio
        asyncio.create_task(_send_task_notification_to_employee(task))
        if task.observer_telegram_id:
            asyncio.create_task(_send_observer_notification(task))

        return _task_to_out(task)


@router.post("/create-with-voice", response_model=TaskOut, status_code=201)
async def create_task_with_voice(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    assigned_to: int = Form(...),
    assigned_name: str = Form(...),
    observer_id: Optional[int] = Form(None),
    observer_name: Optional[str] = Form(None),
    deadline: Optional[str] = Form(None),
    voice_file: Optional[UploadFile] = File(None),
):
    """Овозли хабар билан янги топшириқ яратиш ва Telegram орқали овозли юбориш."""
    voice_url = None

    # Овозли файлни сақлаш
    if voice_file and voice_file.filename:
        # Кенгайтмани аниқлаш
        ext = ".webm"
        fn_lower = voice_file.filename.lower()
        if fn_lower.endswith(".ogg") or (voice_file.content_type and "ogg" in voice_file.content_type):
            ext = ".ogg"
        elif fn_lower.endswith(".mp3") or (voice_file.content_type and "mp3" in voice_file.content_type):
            ext = ".mp3"
        elif fn_lower.endswith(".wav") or (voice_file.content_type and "wav" in voice_file.content_type):
            ext = ".wav"
        elif fn_lower.endswith(".m4a") or (voice_file.content_type and "m4a" in voice_file.content_type):
            ext = ".m4a"

        unique_name = f"voice_{uuid.uuid4().hex[:12]}_{int(time.time())}{ext}"
        upload_dir = Path(__file__).resolve().parent.parent.parent / "static" / "uploads" / "voice_tasks"
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = upload_dir / unique_name

        content = await voice_file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        voice_url = f"/static/uploads/voice_tasks/{unique_name}"
        logger.info("voice_task_saved", filename=unique_name, size=len(content))

    async with AsyncSessionLocal() as session:
        # Ижрочи ходимнинг telegram_id сини олиш
        assigned_tg_id = None
        observer_tg_id = None

        emp = await session.get(AuthorizedEmployee, assigned_to)
        if emp:
            assigned_tg_id = emp.telegram_id

        if observer_id:
            obs = await session.get(AuthorizedEmployee, observer_id)
            if obs:
                observer_tg_id = obs.telegram_id

        # Дедлайнни парсинг қилиш
        deadline_dt = None
        if deadline and deadline.strip():
            try:
                deadline_dt = datetime.fromisoformat(deadline.strip())
            except ValueError:
                raise HTTPException(status_code=400, detail="Нотўғри муддат формати. ISO формат керак: YYYY-MM-DDTHH:MM")

        task = Task(
            title=title,
            description=description,
            assigned_to=assigned_to,
            assigned_name=assigned_name,
            assigned_telegram_id=assigned_tg_id,
            observer_id=observer_id,
            observer_name=observer_name,
            observer_telegram_id=observer_tg_id,
            deadline=deadline_dt,
            status="new",
            voice_url=voice_url,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        logger.info("task_created_with_voice", task_id=task.id, assigned_to=task.assigned_name, has_voice=bool(voice_url))

        # Telegram орқали юбориш (фонда)
        import asyncio
        asyncio.create_task(_send_task_notification_to_employee(task))
        if task.observer_telegram_id:
            asyncio.create_task(_send_observer_notification(task))

        return _task_to_out(task)


@router.put("/{task_id}", response_model=TaskOut)
async def update_task(task_id: int, data: TaskUpdate):
    """Топшириқни таҳрирлаш."""
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Топшириқ топилмади")

        if data.title is not None:
            task.title = data.title
        if data.description is not None:
            task.description = data.description
        if data.status is not None:
            task.status = data.status
        if data.assigned_to is not None:
            task.assigned_to = data.assigned_to
            emp = await session.get(AuthorizedEmployee, data.assigned_to)
            if emp:
                task.assigned_telegram_id = emp.telegram_id
        if data.assigned_name is not None:
            task.assigned_name = data.assigned_name
        if data.observer_id is not None:
            task.observer_id = data.observer_id
            obs = await session.get(AuthorizedEmployee, data.observer_id)
            if obs:
                task.observer_telegram_id = obs.telegram_id
        if data.observer_name is not None:
            task.observer_name = data.observer_name
        if data.deadline is not None:
            try:
                task.deadline = datetime.fromisoformat(data.deadline)
            except ValueError:
                raise HTTPException(status_code=400, detail="Нотўғри муддат формати")

        task.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(task)
        return _task_to_out(task)


@router.put("/{task_id}/respond", response_model=TaskOut)
async def respond_to_task(task_id: int, data: TaskResponse):
    """Ходим жавоби / ҳисоботини қабул қилиш."""
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Топшириқ топилмади")

        task.employee_response = data.text
        if task.status == "new":
            task.status = "in_progress"
        task.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(task)

        # Раҳбарга хабар (Telegram)
        try:
            from services.telegram_bot_service import create_bot_and_dispatcher
            bot, _ = create_bot_and_dispatcher()

            import os
            ceo_chat_id = os.getenv("TELEGRAM_CEO_CHAT_ID") or "5950380558"
            text = (
                f"📩 <b>ХОДИМ ЖАВОБИ — Топшириқ #{task.id}</b>\n\n"
                f"👤 <b>Ижрочи:</b> {task.assigned_name}\n"
                f"📌 <b>Мавзу:</b> {task.title}\n"
                f"💬 <b>Жавоб:</b>\n{data.text}\n"
            )
            await bot.send_message(chat_id=ceo_chat_id, text=text, parse_mode="HTML")
            await bot.session.close()
        except Exception as e:
            logger.error("task_response_notify_failed", error=str(e))

        logger.info("task_response_received", task_id=task.id, employee=task.assigned_name)
        return _task_to_out(task)


@router.put("/{task_id}/status", response_model=TaskOut)
async def update_task_status(task_id: int, data: TaskStatusUpdate):
    """Топшириқ статусини ўзгартириш."""
    valid_statuses = ["new", "in_progress", "completed", "expired"]
    if data.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Нотўғри статус. Мумкин: {', '.join(valid_statuses)}")

    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Топшириқ топилмади")

        task.status = data.status
        task.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(task)
        return _task_to_out(task)


@router.post("/{task_id}/remind")
async def remind_task(task_id: int):
    """Шошилинч эслатма юбориш (дашборддан 🔔 тугмаси)."""
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Топшириқ топилмади")

        if not task.assigned_telegram_id:
            raise HTTPException(status_code=400, detail="Ходимнинг Telegram ID си мавжуд эмас")

        await _send_reminder_to_employee(task, urgent=True)
        return {"ok": True, "detail": f"Эслатма #{task_id} ходимга юборилди"}


@router.delete("/{task_id}")
async def delete_task(task_id: int):
    """Топшириқни ўчириш."""
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Топшириқ топилмади")
        await session.delete(task)
        await session.commit()
        return {"ok": True, "detail": "Топшириқ ўчирилди"}
