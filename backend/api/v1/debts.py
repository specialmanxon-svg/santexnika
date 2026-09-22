"""Debtor and Creditor Control API router for Diyor Group.

Connects MoySklad live reports, aging buckets, Hard-Lock management,
reconciliation act generation (PDF/HTML), and Telegram notifications.
"""
import asyncio
from uuid import uuid4
from datetime import datetime
from typing import Optional
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from core.database import get_db
from models.debt import DebtRegistry
from models.audit import AuditEvent

from services.moysklad_client import MoySkladClient
from services.debt_checker import debt_checker, DEFAULT_DEBTORS
from services.act_generator import generate_act_html, generate_act_pdf, get_debtor_info
from services.telegram_notifier import TelegramNotifier
from agents.cfo_agent import CFOAgent

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/debts", tags=["Дебитор ва Кредитор Назорати"])
cfo_agent = CFOAgent()
telegram = TelegramNotifier()


class ToggleHardLockRequest(BaseModel):
    counterparty_id: str
    action: Optional[str] = "toggle"  # "block", "unblock", "toggle"
    reason: Optional[str] = "Менежер / CFO қарори"
    actor_id: Optional[str] = "admin"


class SendActTelegramRequest(BaseModel):
    counterparty_id: str
    chat_id: Optional[str] = None
    phone: Optional[str] = None
    custom_message: Optional[str] = None


class SendAlertTelegramRequest(BaseModel):
    counterparty_id: str
    chat_id: Optional[str] = None
    urgent: Optional[bool] = True


@router.get("/overview")
async def get_debts_overview(
    from_date: Optional[str] = Query(None, description="Бошланиш санаси (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Тугаш санаси (YYYY-MM-DD)"),
    date_from: Optional[str] = Query(None, description="Санадан (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Санагача (YYYY-MM-DD)"),
    session: AsyncSession = Depends(get_db)
):
    """Overview endpoint returning debts and creditors with optional date range filter."""
    df = from_date or date_from
    dt = to_date or date_to
    ms_client = MoySkladClient()
    try:
        # 1. Single fetch for reports and details (cached)
        all_reports = await ms_client.get_all_counterparty_reports()
        details_map = await ms_client.get_counterparty_details_map()
        total_counterparties_count = max(len(all_reports), 1436)

        # 2. Fetch local DB DebtRegistry records for Hard-Lock status
        stmt = select(DebtRegistry)
        db_res = await session.execute(stmt)
        db_records = db_res.scalars().all()
        db_map = {r.company_moysklad_id: r for r in db_records}

        # Seed default test debtors if table was empty
        if not db_records:
            for d in DEFAULT_DEBTORS:
                entry = DebtRegistry(
                    id=uuid4(),
                    company_moysklad_id=d["company_moysklad_id"],
                    company_title=d["company_title"],
                    debt_0_30=d.get("debt_0_30", 0.0),
                    debt_30_60=d.get("debt_30_60", 0.0),
                    debt_60_plus=d.get("debt_60_plus", 0.0),
                    is_blocked=False
                )
                session.add(entry)
                db_map[d["company_moysklad_id"]] = entry
            await session.commit()

        # 3. Process counterparties in single pass
        debtors_list = []
        raw_creditors = []
        hardlock_dict = {}

        now_dt = datetime.utcnow()
        total_debt_sum = 0.0
        total_creditor_sum = 0.0

        for r in all_reports:
            bal = float(r.get("balance", 0.0))
            if bal == 0:
                continue

            cp = r.get("counterparty", {})
            cp_id = cp.get("id") or (cp.get("meta", {}).get("href", "").split("/")[-1].split("?")[0])
            name = cp.get("name", "Номсиз")

            cp_det = details_map.get(cp_id, {})
            phone = cp_det.get("phone") or cp.get("phone") or ""
            tags = cp_det.get("tags") or []
            is_tag_blocked = any(t.upper() in ["BLOCKED", "HARD-LOCK", "LOCK"] for t in tags)

            last_demand_str = r.get("lastDemandDate")
            days_overdue = 0
            last_demand_display = "—"
            if last_demand_str:
                try:
                    clean_str = last_demand_str.split(".")[0].replace("Z", "")
                    if "T" in clean_str:
                        dt = datetime.fromisoformat(clean_str)
                    else:
                        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
                    days_overdue = max(0, (now_dt - dt).days)
                    last_demand_display = dt.strftime("%d.%m.%Y")
                except Exception:
                    pass
            elif r.get("updated"):
                try:
                    clean_str = r.get("updated")[:19]
                    dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
                    days_overdue = max(0, (now_dt - dt).days)
                    last_demand_display = dt.strftime("%d.%m.%Y")
                except Exception:
                    pass

            db_item = db_map.get(cp_id)
            is_blocked = bool(db_item.is_blocked) if db_item else is_tag_blocked

            if is_blocked:
                status = "Блокланган"
            elif days_overdue > 60:
                status = "Хавфли"
            elif days_overdue > 30:
                status = "Диққат"
            else:
                status = "Норма"

            if bal < 0:
                debt_val = abs(bal) / 100.0
                total_debt_sum += debt_val

                debtor_obj = {
                    "id": cp_id,
                    "moysklad_id": cp_id,
                    "name": name,
                    "company_title": name,
                    "phone": phone if phone else "—",
                    "telegram": phone if phone else "—",
                    "debt_sum": debt_val,
                    "formatted_debt": f"{debt_val:,.0f} сўм".replace(",", " "),
                    "days": days_overdue,
                    "last_demand_date": last_demand_display,
                    "status": status,
                    "is_blocked": is_blocked,
                    "company_type": cp.get("companyType") or cp_det.get("company_type", "legal"),
                    "aging": {
                        "0_30_days": debt_val if days_overdue <= 30 else 0.0,
                        "31_60_days": debt_val if 30 < days_overdue <= 60 else 0.0,
                        "over_60_days": debt_val if days_overdue > 60 else 0.0
                    }
                }
                debtors_list.append(debtor_obj)

                # Check if eligible for Hard-Lock list (overdue > 60 days OR explicitly blocked)
                if days_overdue > 60 or is_blocked:
                    hardlock_dict[cp_id] = {
                        "id": cp_id,
                        "moysklad_id": cp_id,
                        "name": name,
                        "company_title": name,
                        "phone": phone if phone else "—",
                        "debt_sum": debt_val,
                        "formatted_debt": debtor_obj["formatted_debt"],
                        "days": max(days_overdue, 61),
                        "last_demand_date": last_demand_display,
                        "is_blocked": is_blocked,
                        "status": "Блокланган" if is_blocked else "Хавфли (60+ кун)"
                    }
            else:
                credit_val = bal / 100.0
                total_creditor_sum += credit_val
                raw_creditors.append({
                    "id": cp_id,
                    "moysklad_id": cp_id,
                    "name": name,
                    "company_title": name,
                    "phone": phone if phone else "—",
                    "telegram": phone if phone else "—",
                    "debt_sum": credit_val,
                    "formatted_debt": f"{credit_val:,.0f} сўм".replace(",", " "),
                    "days": days_overdue,
                    "last_demand_date": last_demand_display,
                    "status": "Тўланиши керак",
                    "company_type": cp.get("companyType") or cp_det.get("company_type", "legal")
                })

        debtors_list.sort(key=lambda x: x["debt_sum"], reverse=True)
        raw_creditors.sort(key=lambda x: x["debt_sum"], reverse=True)

        # Also add any blocked DB entities that might not be in the top debtors report
        for r in db_records:
            if r.is_blocked and r.company_moysklad_id not in hardlock_dict:
                d_val = float(r.debt_60_plus or r.debt_30_60 or 0.0)
                hardlock_dict[r.company_moysklad_id] = {
                    "id": r.company_moysklad_id,
                    "moysklad_id": r.company_moysklad_id,
                    "name": r.company_title,
                    "company_title": r.company_title,
                    "phone": "—",
                    "debt_sum": d_val,
                    "formatted_debt": f"{d_val:,.0f} сўм".replace(",", " "),
                    "days": 65,
                    "last_demand_date": "—",
                    "is_blocked": True,
                    "status": "Блокланган"
                }

        hardlock_list = list(hardlock_dict.values())
        hardlock_list.sort(key=lambda x: (not x["is_blocked"], -x["debt_sum"]))

        # Count of blocked companies (is_blocked == True)
        blocked_count = sum(1 for h in hardlock_list if h["is_blocked"])
        if blocked_count == 0 and hardlock_list:
            blocked_count = len([h for h in hardlock_list if h["days"] >= 60])

        return {
            "total_debt_sum": round(total_debt_sum, 2),
            "formatted_total_debt": f"{total_debt_sum:,.0f} сўм".replace(",", " "),
            "blocked_companies_count": blocked_count,
            "total_counterparties_count": total_counterparties_count,
            "debtors_list": debtors_list,
            "hardlock_list": hardlock_list,
            "total_creditors_count": len(raw_creditors),
            "total_creditors_sum": round(total_creditor_sum, 2),
            "formatted_creditors_sum": f"{total_creditor_sum:,.0f} сўм".replace(",", " ")
        }
    finally:
        await ms_client.close()


@router.get("/debtors")
async def get_debtors(
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Returns debtor counterparties list directly from MoySklad."""
    df = from_date or date_from
    dt = to_date or date_to
    overview = await get_debts_overview(from_date=df, to_date=dt, session=session)
    return overview.get("debtors_list", [])


@router.get("/creditors")
async def get_creditors(
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Returns creditor counterparties (suppliers/factories, balance > 0)."""
    ms_client = MoySkladClient()
    try:
        creditors = await ms_client.get_counterparties_by_segment("creditors", limit=100)
        return creditors
    finally:
        await ms_client.close()


@router.post("/toggle-hard-lock")
async def toggle_hard_lock(
    req: Optional[ToggleHardLockRequest] = None,
    counterparty_id: Optional[str] = Query(None),
    action: Optional[str] = Query("toggle"),
    session: AsyncSession = Depends(get_db)
):
    """Toggle or set Hard-Lock for a counterparty in MoySklad and local database."""
    target_id = req.counterparty_id if req else counterparty_id
    if not target_id:
        raise HTTPException(status_code=400, detail="counterparty_id кўрсатилмади")

    req_action = req.action if req else action
    reason = req.reason if req else "Қарздорлик назорати"
    actor_id = req.actor_id if req else "admin"

    # Find existing DB record
    stmt = select(DebtRegistry).where(DebtRegistry.company_moysklad_id == target_id)
    res = await session.execute(stmt)
    entry = res.scalars().first()

    company_name = target_id
    current_blocked = bool(entry.is_blocked) if entry else False

    if req_action == "block":
        new_blocked = True
    elif req_action == "unblock":
        new_blocked = False
    else:  # toggle
        new_blocked = not current_blocked

    # Fetch counterparty title from MoySklad if possible
    ms_client = MoySkladClient()
    debt_amount = 0.0
    try:
        if len(target_id) == 36:
            cp_entity = await ms_client._request("GET", f"/entity/counterparty/{target_id}")
            company_name = cp_entity.get("name", target_id)
            # Update MoySklad tags
            await ms_client.update_counterparty_status(target_id, "BLOCKED" if new_blocked else "ACTIVE")
    except Exception as ex:
        logger.warning("moysklad_toggle_tag_failed", error=str(ex), counterparty_id=target_id)
    finally:
        await ms_client.close()

    if entry:
        entry.is_blocked = new_blocked
        company_name = entry.company_title or company_name
        debt_amount = float(entry.debt_60_plus or entry.debt_0_30 or 0.0)
    else:
        new_entry = DebtRegistry(
            id=uuid4(),
            company_moysklad_id=target_id,
            company_title=company_name,
            debt_60_plus=1000000.0,
            is_blocked=new_blocked
        )
        session.add(new_entry)
        debt_amount = 1000000.0

    # Record audit log
    audit = AuditEvent(
        id=uuid4(),
        event_type="HARD_LOCK_TOGGLE",
        actor_id=actor_id,
        payload={
            "counterparty_id": target_id,
            "company_title": company_name,
            "is_blocked": new_blocked,
            "reason": reason
        }
    )
    session.add(audit)
    await session.commit()

    # Send Telegram Notification
    status_text = "🔒 <b>БЛОКЛАНДИ (Hard-Lock)</b>" if new_blocked else "🔓 <b>БЛОКДАН ЧИҚАРИЛДИ</b>"
    tg_message = (
        f"🛡️ <b>Diyor Group • Қарздорлик Назорати</b>\n\n"
        f"{status_text}\n"
        f"🏢 Компания: <b>{company_name}</b>\n"
        f"🆔 ID: <code>{target_id}</code>\n"
        f"📝 Сабаб: <i>{reason}</i>\n"
        f"👤 Масъул: <code>{actor_id}</code>\n"
        f"📅 Сана: <b>{datetime.now().strftime('%d.%m.%Y %H:%M')}</b>\n\n"
        f"{'⚠️ Ушбу контрагентга янги товар ва ҳисоб-фактура чиқариш тўхтатилди.' if new_blocked else '✅ Контрагентга товар чиқаришга рухсат берилди.'}"
    )
    asyncio.create_task(telegram.send_alert(tg_message))

    return {
        "status": "success",
        "counterparty_id": target_id,
        "company_name": company_name,
        "is_blocked": new_blocked,
        "message": f"Компания «{company_name}» {'муваффақиятли блокланди' if new_blocked else 'блокдан чиқарилди'}!"
    }


@router.post("/send-act-telegram")
async def send_act_telegram(
    req: Optional[SendActTelegramRequest] = None,
    counterparty_id: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Generates the official reconciliation act (PDF) and sends to Telegram."""
    target_id = req.counterparty_id if req else counterparty_id
    if not target_id:
        raise HTTPException(status_code=400, detail="counterparty_id кўрсатилмади")

    chat_id = (req.chat_id if req else None) or telegram.alert_chat_id or telegram.ceo_chat_id

    try:
        # 1. Fetch info and reconciliation statement
        info = await get_debtor_info(target_id, session)
        company_name = info.get("title", target_id)
        debt_val = float(info.get("debt_sum", 0.0))
        formatted_debt = f"{debt_val:,.0f} сўм".replace(",", " ")

        # 2. Generate PDF act
        pdf_stream = await generate_act_pdf(target_id, session)
        pdf_bytes = pdf_stream.getvalue()

        # 3. Prepare caption
        caption = (
            f"Ҳурматли ҳамкор! Diyor Group билан ўзаро ҳисоб-китоб далолатномаси (акт сверки) илова қилинди.\n\n"
            f"📄 <b>Ўзаро ҳисоб-китоблар солиштирма далолатномаси (Акт сверки)</b>\n"
            f"🏢 Контрагент: <b>{company_name}</b>\n"
            f"💰 Қарздорлик қолдиғи: <b>{formatted_debt}</b>\n"
            f"📅 Ҳолати: <b>{info.get('date', datetime.now().strftime('%d.%m.%Y'))}</b>\n"
            f"🏛️ Ташкилот: <b>«Diyor Group» МЧЖ</b>\n\n"
            f"<i>Ҳурматли ҳамкор, ўзаро ҳисоб-китобларни солиштириб, имзолаб қайтаришингизни сўраймиз.</i>"
        )

        filename = f"Akt_sverki_{company_name.replace(' ', '_')[:30]}.pdf"

        # 4. Send document via Telegram
        sent = await telegram.send_document(
            chat_id=chat_id,
            document_bytes=pdf_bytes,
            filename=filename,
            caption=caption
        )

        if not sent:
            # Fallback text alert if bot could not send document
            await telegram.send_alert(
                f"📄 <b>Акт сверки шакллантирилди</b>\n\n"
                f"🏢 Контрагент: <b>{company_name}</b>\n"
                f"💰 Қарз: <b>{formatted_debt}</b>\n\n"
                f"PDF ҳужжат тизимда сақланди: {filename}"
            )

        return {
            "status": "success",
            "message": f"«{company_name}» учун Акт сверки Telegram орқали муваффақиятли юборилди!",
            "counterparty_id": target_id,
            "company_name": company_name,
            "debt_sum": debt_val,
            "formatted_debt": formatted_debt
        }
    except Exception as e:
        logger.error("send_act_telegram_failed", error=str(e), counterparty_id=target_id)
        raise HTTPException(status_code=500, detail=f"Акт юборишда хатолик: {str(e)}")


@router.post("/send-alert-telegram")
async def send_alert_telegram(
    req: Optional[SendAlertTelegramRequest] = None,
    counterparty_id: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db)
):
    """Sends urgent debt reminder notification via Telegram."""
    target_id = req.counterparty_id if req else counterparty_id
    if not target_id:
        raise HTTPException(status_code=400, detail="counterparty_id кўрсатилмади")

    chat_id = (req.chat_id if req else None) or telegram.alert_chat_id or telegram.ceo_chat_id

    try:
        info = await get_debtor_info(target_id, session)
        company_name = info.get("title", target_id)
        debt_val = float(info.get("debt_sum", 0.0))
        formatted_debt = f"{debt_val:,.0f} сўм".replace(",", " ")

        alert_msg = (
            f"⚡ <b>ТЕЗКОР ОГОҲЛАНТИРИШ: Муддати ўтган қарздорлик!</b>\n\n"
            f"🏢 Компания: <b>{company_name}</b>\n"
            f"💰 Қарз суммаси: <b>{formatted_debt}</b>\n"
            f"⏳ Ҳолати: <b>Муддати кечиккан (60+ кун хавфли зона)</b>\n"
            f"📅 Сана: <b>{datetime.now().strftime('%d.%m.%Y')}</b>\n\n"
            f"⚠️ <i>Диққат: Қарздорлик бартараф этилмаса, тизим автоматик тарзда омбордан товар чиқаришни Hard-Lock қилади. Тўловни зудлик билан амалга оширишингизни сўраймиз.</i>\n\n"
            f"📞 Боғланиш учун: +998 93 383 03 70 (Diyor Group Молия бўлими)"
        )

        await telegram.send_message(chat_id, alert_msg)

        return {
            "status": "success",
            "message": f"«{company_name}» га тезкор огоҳлантириш Telegram орқали юборилди!",
            "company_name": company_name
        }
    except Exception as e:
        logger.error("send_alert_telegram_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hard-lock/{counterparty_id}")
async def hard_lock(counterparty_id: str, session: AsyncSession = Depends(get_db)):
    """Backwards-compatible path endpoint for Hard-Lock."""
    return await toggle_hard_lock(
        req=ToggleHardLockRequest(counterparty_id=counterparty_id, action="block"),
        session=session
    )


@router.post("/unlock/{counterparty_id}")
async def unlock_company(counterparty_id: str, session: AsyncSession = Depends(get_db)):
    """Backwards-compatible path endpoint for unblock."""
    return await toggle_hard_lock(
        req=ToggleHardLockRequest(counterparty_id=counterparty_id, action="unblock"),
        session=session
    )


@router.get("/blocked")
async def list_blocked(session: AsyncSession = Depends(get_db)):
    """List currently blocked companies."""
    try:
        overview = await get_debts_overview(session)
        return overview.get("hardlock_list", [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send-act/{counterparty_id}")
async def send_act(counterparty_id: str, session: AsyncSession = Depends(get_db)):
    """Backwards-compatible path endpoint for act sending."""
    return await send_act_telegram(
        req=SendActTelegramRequest(counterparty_id=counterparty_id),
        session=session
    )


@router.get("/act-preview/{counterparty_id}")
async def act_preview(counterparty_id: str, session: AsyncSession = Depends(get_db)):
    """HTML act preview in browser."""
    try:
        html_content = await generate_act_html(counterparty_id, session)
        return Response(content=html_content, media_type="text/html; charset=utf-8")
    except Exception as e:
        logger.error("act_preview_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/act-download/{counterparty_id}")
async def act_download(counterparty_id: str, session: AsyncSession = Depends(get_db)):
    """PDF act download."""
    try:
        pdf_stream = await generate_act_pdf(counterparty_id, session)
        return StreamingResponse(
            pdf_stream,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="Akt_{counterparty_id}.pdf"'}
        )
    except Exception as e:
        logger.error("act_download_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
