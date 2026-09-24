"""Топшириқлар ва Назорат — Tasks CRUD API."""
import os
import time
import uuid
from pathlib import Path
import structlog
from typing import Optional, List, Any, Dict
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
    title: Optional[str] = Field(None, description="Топшириқ мавзуси")
    description: Optional[str] = Field(None, description="Батафсил матн/сўров")
    title_and_description: Optional[str] = Field(None, description="Топшириқ мазмуни / тўлиқ матни")
    assigned_to: Optional[Any] = Field(None, description="Ижрочи ходим ID")
    assigned_name: Optional[str] = Field(None, description="Ижрочи ходим исми")
    assignee_name: Optional[str] = Field(None, description="Ижрочи исми (алиас)")
    assignee_chat_id: Optional[int] = Field(None, description="Ижрочи Telegram ID")
    creator_name: Optional[str] = Field(None, description="Топшириқ берган шахс исми")
    creator_chat_id: Optional[int] = Field(None, description="Топшириқ берган шахс telegram id'си")
    observer_id: Optional[Any] = Field(None, description="Кузатувчи ходим ID")
    observer_name: Optional[str] = Field(None, description="Кузатувчи ходим исми")
    deadline: Optional[str] = Field(None, description="Муддат ISO формат (YYYY-MM-DDTHH:MM)")
    voice_url: Optional[str] = Field(None, description="Овозли хабар URL")
    voice_file_id: Optional[str] = Field(None, description="Telegram voice file ID")


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    title_and_description: Optional[str] = None
    creator_name: Optional[str] = None
    creator_chat_id: Optional[int] = None
    assigned_to: Optional[int] = None
    assigned_name: Optional[str] = None
    assignee_name: Optional[str] = None
    assignee_chat_id: Optional[int] = None
    observer_id: Optional[int] = None
    observer_name: Optional[str] = None
    deadline: Optional[str] = None
    status: Optional[str] = None
    voice_url: Optional[str] = None
    voice_file_id: Optional[str] = None


class TaskResponse(BaseModel):
    text: str = Field(..., description="Ходим жавоби / ҳисоботи")


class TaskStatusUpdate(BaseModel):
    status: str = Field(..., description="Янги статус: new, in_progress, completed, overdue, expired")


class TaskOut(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    title_and_description: Optional[str] = None
    creator_name: Optional[str] = None
    creator_chat_id: Optional[int] = None
    assigned_to: int
    assigned_name: str
    assigned_telegram_id: Optional[int] = None
    assignee_name: Optional[str] = None
    assignee_chat_id: Optional[int] = None
    observer_id: Optional[int] = None
    observer_name: Optional[str] = None
    observer_telegram_id: Optional[int] = None
    deadline: Optional[str] = None
    status: str
    employee_response: Optional[str] = None
    voice_url: Optional[str] = None
    voice_file_id: Optional[str] = None
    reminder_sent: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ── Helper: Ходим маълумотлари ва Telegram Chat ID олиш ─────────────

async def get_employee_by_id_or_name(session, identifier: Any, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Ходимнинг ID, Telegram ID, Moysklad ID ёки исми бўйича
    authorized_employees жадвалидан маълумотларини ва telegram_chat_id сини тортиб олиш.
    """
    if identifier is None and not name:
        return None

    stmt = select(AuthorizedEmployee).where(AuthorizedEmployee.is_active == 1)
    res = await session.execute(stmt)
    employees = res.scalars().all()

    # 1. ID бўйича қидириш (int PK ёки telegram_id)
    try:
        if identifier is not None and str(identifier).strip().isdigit():
            iid = int(str(identifier).strip())
            for emp in employees:
                if emp.id == iid or emp.telegram_id == iid:
                    return {
                        "id": emp.id,
                        "name": emp.employee_name,
                        "telegram_chat_id": emp.telegram_id,
                        "phone": emp.phone_number,
                    }
    except Exception:
        pass

    # 2. MoySklad UUID бўйича қидириш
    if identifier:
        id_str = str(identifier).strip().lower()
        for emp in employees:
            if emp.moysklad_id and str(emp.moysklad_id).strip().lower() == id_str:
                return {
                    "id": emp.id,
                    "name": emp.employee_name,
                    "telegram_chat_id": emp.telegram_id,
                    "phone": emp.phone_number,
                }

    # 3. Исм бўйича қидириш (name параметри ёки identifier агар матн бўлса)
    search_name = (name or (str(identifier) if not str(identifier).isdigit() else "")).strip().lower()
    if search_name:
        for emp in employees:
            if emp.employee_name.strip().lower() == search_name:
                return {
                    "id": emp.id,
                    "name": emp.employee_name,
                    "telegram_chat_id": emp.telegram_id,
                    "phone": emp.phone_number,
                }
        # Қисман мослик (масалан: 'Latipov', 'Джумаева')
        s_words = [w for w in search_name.replace(".", " ").split() if len(w) > 2]
        for emp in employees:
            emp_lower = emp.employee_name.lower()
            if any(w in emp_lower for w in s_words):
                return {
                    "id": emp.id,
                    "name": emp.employee_name,
                    "telegram_chat_id": emp.telegram_id,
                    "phone": emp.phone_number,
                }

    return None


# ── Helper: Telegram Bot олиш ───────────────────────────────────

def get_bot():
    """Хабар юбориш учун тоза aiogram Bot инстансини қайтариш."""
    from config import settings
    from aiogram import Bot
    return Bot(token=settings.telegram_bot_token)


# ── Helper: Telegram хабар юбориш ───────────────────────────────

async def _send_task_notification_to_employee(task: Task, assignee: Optional[Dict[str, Any]] = None, observer: Optional[Dict[str, Any]] = None):
    """Ижрочига Telegram орқали топшириқ (овозли ёки матнли) хабарини юбориш."""
    chat_id = task.assigned_telegram_id or (assignee.get("telegram_chat_id") if assignee else None)
    emp_name = (assignee.get("name") if assignee else task.assigned_name) or "Ходим"
    obs_name = (observer.get("name") if observer else task.observer_name) or "Йўқ"
    creator_str = task.creator_name or "Раҳбарият"

    if not chat_id:
        print(f"WARNING: Ходимда telegram_chat_id мавжуд эмас! ({emp_name})")
        logger.warning("task_notify_skip_no_tg_id", task_id=task.id, employee=emp_name)
        return

    try:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

        bot = get_bot()

        deadline_str = "—"
        if task.deadline:
            dl = task.deadline
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc).astimezone(UZ_TZ)
            deadline_str = dl.strftime("%d.%m.%Y %H:%M")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🟢 Қабул қилдим", callback_data=f"task_accept_{task.id}"),
                InlineKeyboardButton(text="✅ Бажардим", callback_data=f"task_done_{task.id}"),
            ],
            [
                InlineKeyboardButton(text="✍️ Изоҳ қолдириш", callback_data=f"task_respond_{task.id}"),
            ]
        ])

        voice_sent = False
        if task.voice_url:
            voice_disk_path = Path(__file__).resolve().parent.parent.parent / task.voice_url.lstrip("/")
            if voice_disk_path.exists():
                caption = (
                    f"🎙 <b>СИЗГА ЯНГИ ОВОЗЛИ ТОПШИРИҚ БЕРИЛДИ! #{task.id}</b>\n\n"
                    f"👤 <b>Ким берди:</b> {creator_str}\n"
                    f"📌 <b>Мавзу:</b> {task.title}\n"
                    f"📝 <b>Тафсилот:</b> {task.description or '—'}\n"
                    f"⏰ <b>Муддат:</b> {deadline_str}\n"
                    f"👁 <b>Кузатувчи:</b> {obs_name}\n\n"
                    f"<i>Илтимос, вазифани ўз вақтида бажаринг!</i>"
                )
                try:
                    await bot.send_voice(
                        chat_id=chat_id,
                        voice=FSInputFile(str(voice_disk_path)),
                        caption=caption,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
                    voice_sent = True
                    print(f"DEBUG: Овозли топшириқ {emp_name} га муваффақиятли кетди.")
                    logger.info("task_voice_notification_sent", task_id=task.id, chat_id=chat_id)
                except Exception as ve:
                    print(f"ERROR: Овоз юборишда хатолик: {ve}")
                    logger.warning("send_voice_failed_fallback_to_text", error=str(ve))

        if not voice_sent:
            msg = (
                f"📋 <b>СИЗГА ЯНГИ ТОПШИРИҚ БЕРИЛДИ! #{task.id}</b>\n\n"
                f"👤 <b>Ким берди:</b> {creator_str}\n"
                f"📌 <b>Мавзу:</b> {task.title}\n"
                f"📝 <b>Тафсилот:</b> {task.description or '—'}\n"
                f"⏰ <b>Муддат:</b> {deadline_str}\n"
                f"👁 <b>Кузатувчи:</b> {obs_name}\n\n"
                f"<i>Илтимос, вазифани ўз вақтида бажаринг!</i>"
            )
            await bot.send_message(
                chat_id=chat_id,
                text=msg,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            print(f"DEBUG: Топшириқ {emp_name} га муваффақиятли кетди.")
            logger.info("task_text_notification_sent", task_id=task.id, chat_id=chat_id)

        await bot.session.close()
    except Exception as e:
        print(f"ERROR: Ижрочига хабар юборишда хатолик: {e}")
        logger.error("task_notification_failed", task_id=task.id, error=str(e))


async def _send_observer_notification(task: Task, assignee: Optional[Dict[str, Any]] = None, observer: Optional[Dict[str, Any]] = None):
    """Кузатувчига маълумот учун овозли ёки матнли хабар юбориш."""
    chat_id = task.observer_telegram_id or (observer.get("telegram_chat_id") if observer else None)
    obs_name = (observer.get("name") if observer else task.observer_name) or "Кузатувчи"
    emp_name = (assignee.get("name") if assignee else task.assigned_name) or "Ходим"

    if not chat_id:
        print(f"WARNING: Кузатувчида telegram_chat_id мавжуд эмас! ({obs_name})")
        return

    try:
        from aiogram.types import FSInputFile
        bot = get_bot()

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
                    f"👁 <b>НАЗОРАТ: Янги овозли топшириқ яратилди</b>\n\n"
                    f"👤 <b>Ижрочи:</b> {emp_name}\n"
                    f"📌 <b>Мавзу:</b> {task.title}\n"
                    f"⏰ <b>Муддат:</b> {deadline_str}\n"
                    f"📝 <b>Тафсилот:</b> {task.description or '—'}"
                )
                try:
                    await bot.send_voice(
                        chat_id=chat_id,
                        voice=FSInputFile(str(voice_disk_path)),
                        caption=caption,
                        parse_mode="HTML",
                    )
                    voice_sent = True
                    print(f"DEBUG: Кузатувчи {obs_name} га овозли хабар кетди.")
                    logger.info("observer_voice_notification_sent", task_id=task.id, chat_id=chat_id)
                except Exception as ve:
                    print(f"ERROR: Кузатувчига овоз юборишда хатолик: {ve}")
                    logger.warning("observer_send_voice_failed", error=str(ve))

        if not voice_sent:
            obs_msg = (
                f"👁 <b>НАЗОРАТ: Янги топшириқ яратилди</b>\n\n"
                f"👤 <b>Ижрочи:</b> {emp_name}\n"
                f"📌 <b>Мавзу:</b> {task.title}\n"
                f"⏰ <b>Муддат:</b> {deadline_str}\n"
                f"📝 <b>Тафсилот:</b> {task.description or '—'}"
            )
            await bot.send_message(
                chat_id=chat_id,
                text=obs_msg,
                parse_mode="HTML",
            )
            print(f"DEBUG: Кузатувчи {obs_name} га хабар кетди.")
            logger.info("observer_notification_sent", task_id=task.id, chat_id=chat_id)

        await bot.session.close()
    except Exception as e:
        print(f"ERROR: Кузатувчига хабар юборишда хатолик: {e}")
        logger.error("observer_notification_failed", task_id=task.id, error=str(e))


async def _send_reminder_to_employee(task: Task, urgent: bool = False):
    """Ходимга эслатма юбориш."""
    if not task.assigned_telegram_id:
        return

    try:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        bot = get_bot()

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
        title_and_description=f"{t.title}\n{t.description}" if t.description else t.title,
        creator_name=t.creator_name,
        creator_chat_id=t.creator_chat_id,
        assigned_to=t.assigned_to,
        assigned_name=t.assigned_name,
        assigned_telegram_id=t.assigned_telegram_id,
        assignee_name=t.assigned_name,
        assignee_chat_id=t.assigned_telegram_id,
        observer_id=t.observer_id,
        observer_name=t.observer_name,
        observer_telegram_id=t.observer_telegram_id,
        deadline=t.deadline.isoformat() if t.deadline else None,
        status=t.status,
        employee_response=t.employee_response,
        voice_url=t.voice_url,
        voice_file_id=t.voice_file_id,
        reminder_sent=t.reminder_sent or 0,
        created_at=t.created_at.isoformat() if t.created_at else None,
        updated_at=t.updated_at.isoformat() if t.updated_at else None,
    )


async def _notify_creator_task_completed(task: Task):
    """Топшириқ бажарилганда раҳбар ёки яратувчига хабар бериш."""
    target_chat = task.creator_chat_id
    if not target_chat:
        import os
        from config import settings
        target_chat = os.getenv("TELEGRAM_CEO_CHAT_ID") or getattr(settings, "telegram_ceo_chat_id", "5950380558")

    if not target_chat:
        return

    try:
        bot = get_bot()
        now_local = datetime.now(UZ_TZ)
        text = (
            f"🏁 <b>ВАЗИФА БАЖАРИЛДИ!</b>\n\n"
            f"Ходим <b>{task.assigned_name}</b> вазифани бажарди.\n\n"
            f"📋 <b>Топшириқ #{task.id}:</b> {task.title}\n"
            f"⏰ <b>Бажарилди:</b> {now_local.strftime('%d.%m.%Y %H:%M')}\n"
            f"🌐 <a href='https://santexnika.onrender.com/dashboard#tasks'>Дашбордда кўриш</a>"
        )
        await bot.send_message(chat_id=target_chat, text=text, parse_mode="HTML")
        await bot.session.close()
    except Exception as e:
        logger.warning("notify_creator_completed_failed", error=str(e))


# ── Endpoints ───────────────────────────────────────────────────

@router.get("/", response_model=List[TaskOut])
@router.get("", response_model=List[TaskOut])
@router.get("/list", response_model=List[TaskOut])
async def list_tasks(
    status: Optional[str] = Query(None, description="Статус фильтри: new, in_progress, completed, expired, overdue"),
):
    """Барча топшириқлар рўйхати (статус бўйича фильтр)."""
    async with AsyncSessionLocal() as session:
        q = select(Task).order_by(desc(Task.created_at))
        if status:
            st = status.lower().strip()
            if st == "overdue":
                st = "expired"
            q = q.where(Task.status == st)
        result = await session.execute(q)
        tasks = result.scalars().all()
        return [_task_to_out(t) for t in tasks]


@router.post("/", response_model=TaskOut, status_code=201)
@router.post("", response_model=TaskOut, status_code=201)
@router.post("/create", response_model=TaskOut, status_code=201)
async def create_task(data: TaskCreate):
    """Янги топшириқ яратиш ва Telegram орқали хабар юбориш."""
    async with AsyncSessionLocal() as session:
        # Title and description extraction
        task_title = data.title
        task_desc = data.description
        if not task_title and data.title_and_description:
            lines = [ln.strip() for ln in data.title_and_description.strip().split("\n") if ln.strip()]
            if lines:
                task_title = lines[0][:200]
                task_desc = "\n".join(lines[1:]) if len(lines) > 1 else None
            else:
                task_title = "Янги топшириқ"
        elif not task_title:
            task_title = "Янги топшириқ"

        # 1. Ходимларнинг telegram_chat_id сини топиш
        assign_target = data.assigned_to or data.assignee_chat_id or data.assignee_name or data.assigned_name
        assign_name = data.assigned_name or data.assignee_name
        assignee = await get_employee_by_id_or_name(session, assign_target, assign_name)
        observer = await get_employee_by_id_or_name(session, data.observer_id, data.observer_name) if data.observer_id else None

        assigned_tg_id = data.assignee_chat_id or (assignee.get("telegram_chat_id") if assignee else None)
        observer_tg_id = observer.get("telegram_chat_id") if observer else None
        assigned_name = assignee.get("name") if assignee else (data.assignee_name or data.assigned_name or "Ходим")
        observer_name = observer.get("name") if observer else data.observer_name
        assigned_to_id = assignee.get("id") if assignee else (int(data.assigned_to) if data.assigned_to and str(data.assigned_to).isdigit() else 0)
        observer_to_id = observer.get("id") if observer else (int(data.observer_id) if data.observer_id and str(data.observer_id).isdigit() else None)

        if not assigned_tg_id:
            print(f"WARNING: Ходимда telegram_chat_id мавжуд эмас! (Ижрочи: {assigned_name}, ID: {data.assigned_to})")
            logger.warning("assignee_no_telegram_id", employee=assigned_name, id=data.assigned_to)
        else:
            print(f"DEBUG: Ижрочи топилди: {assigned_name} (chat_id: {assigned_tg_id})")

        if data.observer_id and not observer_tg_id:
            print(f"WARNING: Кузатувчида telegram_chat_id мавжуд эмас! (Кузатувчи: {data.observer_name}, ID: {data.observer_id})")
            logger.warning("observer_no_telegram_id", observer=data.observer_name, id=data.observer_id)
        elif observer_tg_id:
            print(f"DEBUG: Кузатувчи топилди: {observer_name} (chat_id: {observer_tg_id})")

        # Дедлайнни парсинг қилиш
        deadline_dt = None
        if data.deadline:
            try:
                deadline_dt = datetime.fromisoformat(data.deadline)
            except ValueError:
                raise HTTPException(status_code=400, detail="Нотўғри муддат формати. ISO формат керак: YYYY-MM-DDTHH:MM")

        task = Task(
            title=task_title,
            description=task_desc,
            creator_name=data.creator_name or "Раҳбарият",
            creator_chat_id=data.creator_chat_id,
            assigned_to=assigned_to_id,
            assigned_name=assigned_name,
            assigned_telegram_id=assigned_tg_id,
            observer_id=observer_to_id,
            observer_name=observer_name,
            observer_telegram_id=observer_tg_id,
            deadline=deadline_dt,
            status="new",
            voice_url=data.voice_url,
            voice_file_id=data.voice_file_id,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        logger.info("task_created", task_id=task.id, assigned_to=task.assigned_name)

        # Telegram хабарномаларни юбориш
        await _send_task_notification_to_employee(task, assignee=assignee, observer=observer)
        if observer_tg_id:
            await _send_observer_notification(task, assignee=assignee, observer=observer)

        return _task_to_out(task)


@router.post("/create-with-voice", response_model=TaskOut, status_code=201)
async def create_task_with_voice(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    assigned_to: Any = Form(...),
    assigned_name: str = Form(...),
    creator_name: Optional[str] = Form(None),
    creator_chat_id: Optional[int] = Form(None),
    observer_id: Optional[Any] = Form(None),
    observer_name: Optional[str] = Form(None),
    deadline: Optional[str] = Form(None),
    voice_file: Optional[UploadFile] = File(None),
):
    """Овозли хабар билан янги топшириқ яратиш ва Telegram орқали овозли юбориш."""
    voice_url = None

    # Овозли файлни сақлаш
    if voice_file and voice_file.filename:
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
        # 1. Ходимларнинг telegram_chat_id сини топиш
        assignee = await get_employee_by_id_or_name(session, assigned_to, assigned_name)
        observer = await get_employee_by_id_or_name(session, observer_id, observer_name) if observer_id else None

        assigned_tg_id = assignee.get("telegram_chat_id") if assignee else None
        observer_tg_id = observer.get("telegram_chat_id") if observer else None
        final_assigned_name = assignee.get("name") if assignee else assigned_name
        final_observer_name = observer.get("name") if observer else observer_name
        assigned_to_id = assignee.get("id") if assignee else (int(assigned_to) if str(assigned_to).isdigit() else 0)
        observer_to_id = observer.get("id") if observer else (int(observer_id) if observer_id and str(observer_id).isdigit() else None)

        if not assigned_tg_id:
            print(f"WARNING: Ходимда telegram_chat_id мавжуд эмас! (Ижрочи: {assigned_name}, ID: {assigned_to})")
            logger.warning("assignee_no_telegram_id", employee=assigned_name, id=assigned_to)
        else:
            print(f"DEBUG: Ижрочи топилди: {final_assigned_name} (chat_id: {assigned_tg_id})")

        if observer_id and not observer_tg_id:
            print(f"WARNING: Кузатувчида telegram_chat_id мавжуд эмас! (Кузатувчи: {observer_name}, ID: {observer_id})")
            logger.warning("observer_no_telegram_id", observer=observer_name, id=observer_id)
        elif observer_tg_id:
            print(f"DEBUG: Кузатувчи топилди: {final_observer_name} (chat_id: {observer_tg_id})")

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
            creator_name=creator_name or "Раҳбарият",
            creator_chat_id=creator_chat_id,
            assigned_to=assigned_to_id,
            assigned_name=final_assigned_name,
            assigned_telegram_id=assigned_tg_id,
            observer_id=observer_to_id,
            observer_name=final_observer_name,
            observer_telegram_id=observer_tg_id,
            deadline=deadline_dt,
            status="new",
            voice_url=voice_url,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        logger.info("task_created_with_voice", task_id=task.id, assigned_to=task.assigned_name, has_voice=bool(voice_url))

        # Telegram орқали юбориш
        await _send_task_notification_to_employee(task, assignee=assignee, observer=observer)
        if observer_tg_id:
            await _send_observer_notification(task, assignee=assignee, observer=observer)

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
        if data.creator_name is not None:
            task.creator_name = data.creator_name
        if data.creator_chat_id is not None:
            task.creator_chat_id = data.creator_chat_id
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
            bot = get_bot()
            ceo_chat_id = task.creator_chat_id or os.getenv("TELEGRAM_CEO_CHAT_ID") or "5950380558"
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
@router.patch("/{task_id}/status", response_model=TaskOut)
async def update_task_status(task_id: int, data: TaskStatusUpdate):
    """Топшириқ статусини ўзгартириш."""
    new_status = data.status.lower().strip()
    if new_status == "overdue":
        new_status = "expired"

    valid_statuses = ["new", "in_progress", "completed", "expired"]
    if new_status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Нотўғри статус. Мумкин: {', '.join(valid_statuses)}")

    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Топшириқ топилмади")

        prev_status = task.status
        task.status = new_status
        task.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(task)

        # Агар вазифа бажарилган бўлса, топшириқ берган раҳбарга бот орқали хабар юбориш
        if new_status == "completed" and prev_status != "completed":
            await _notify_creator_task_completed(task)

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
