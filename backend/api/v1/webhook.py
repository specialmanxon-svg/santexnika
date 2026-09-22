"""MoySklad Webhook & Hard-Lock Demand Interceptor API.

Intercepts MoySklad 'demand' (Отгрузка) documents in real-time.
If counterparty is in Hard-Lock (DebtRegistry is_blocked=True or tagged BLOCKED),
it automatically unconducts the document (applicable=False) and sends an instant
urgent alert to Telegram leadership.
"""
from datetime import datetime
from uuid import uuid4
import structlog
from fastapi import APIRouter, Request, BackgroundTasks, HTTPException, Header, status
from sqlalchemy import select

from config import settings
from core.database import async_session_factory
from core.security import verify_moysklad_webhook
from models.debt import DebtRegistry
from models.audit import AuditEvent
from services.moysklad_client import MoySkladClient
from services.telegram_notifier import TelegramNotifier

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["MoySklad Webhooks & Hard-Lock Interceptor"])

telegram = TelegramNotifier()

# Track already intercepted demand IDs to avoid duplicate alerts during background polling
_ALREADY_INTERCEPTED: set[str] = set()


async def intercept_demand_document(demand_id: str, action: str = "CREATE") -> dict:
    """
    Core inspection and interception logic for a MoySklad demand document.
    """
    if demand_id in _ALREADY_INTERCEPTED and action == "AUDIT_SCAN":
        return {"status": "skipped", "reason": "Already intercepted", "demand_id": demand_id}

    ms_client = MoySkladClient()
    try:
        demand = await ms_client.get_demand(demand_id)
        if not demand or not isinstance(demand, dict):
            logger.warning("demand_not_found_in_moysklad", demand_id=demand_id)
            return {"status": "error", "reason": "Demand document not found"}

        doc_number = demand.get("name", "N/A")
        applicable = bool(demand.get("applicable", False))
        sum_tiyin = demand.get("sum", 0.0) or 0.0
        sum_uzs = float(sum_tiyin) / 100.0

        agent = demand.get("agent", {}) or {}
        agent_id = agent.get("id")
        if not agent_id and "meta" in agent:
            agent_id = agent["meta"].get("href", "").split("/")[-1]

        agent_name = agent.get("name", "Номаълум мижоз")

        owner = demand.get("owner", {}) or {}
        owner_name = owner.get("name", "Кўрсатилмаган")

        if not agent_id:
            logger.warning("demand_has_no_agent", demand_id=demand_id, doc_number=doc_number)
            return {"status": "skipped", "reason": "No counterparty linked to demand"}

        # 1. Check Hard-Lock status in local DB (DebtRegistry)
        is_blocked = False
        total_debt = 0.0
        company_title = agent_name

        async with async_session_factory() as session:
            stmt = select(DebtRegistry).where(DebtRegistry.company_moysklad_id == agent_id)
            res = await session.execute(stmt)
            entry = res.scalars().first()

            if entry:
                is_blocked = bool(entry.is_blocked)
                company_title = entry.company_title or agent_name
                total_debt = float(entry.debt_60_plus or 0) + float(entry.debt_30_60 or 0) + float(entry.debt_0_30 or 0)
                if total_debt == 0.0 and entry.debt_60_plus:
                    total_debt = float(entry.debt_60_plus)

        # 2. Also inspect tags in counterparty entity if not found in local DB
        agent_tags = agent.get("tags", []) or []
        if not is_blocked:
            if any(str(t).upper() in ["БЛОК", "BLOCKED", "HARD-LOCK", "ҲУЖЖАТ ЧИҚАРИШ ТАҚИҚЛАНГАН", "ТАҚИҚЛАНГАН"] for t in agent_tags):
                is_blocked = True

        # If counterparty is NOT blocked, allow normal operation
        if not is_blocked:
            return {
                "status": "allowed",
                "demand_id": demand_id,
                "doc_number": doc_number,
                "counterparty": agent_name,
                "is_blocked": False
            }

        # 3. INTERCEPT: Counterparty IS BLOCKED (Hard-Lock active)
        # Fetch LIVE real debt directly from MoySklad API (/report/counterparty/{id})
        real_debt_from_ms = await ms_client.get_counterparty_real_debt(agent_id)
        if real_debt_from_ms > 0:
            total_debt = real_debt_from_ms
            # Sync real debt into local DB for accuracy
            try:
                async with async_session_factory() as session:
                    stmt = select(DebtRegistry).where(DebtRegistry.company_moysklad_id == agent_id)
                    db_res = await session.execute(stmt)
                    db_entry = db_res.scalars().first()
                    if db_entry:
                        db_entry.debt_60_plus = total_debt
                        await session.commit()
            except Exception as sync_err:
                logger.warning("failed_to_sync_real_debt_to_db", error=str(sync_err))

        logger.warning(
            "hard_lock_shipment_detected",
            demand_id=demand_id,
            doc_number=doc_number,
            counterparty=company_title,
            applicable=applicable,
            sum_uzs=sum_uzs,
            total_debt_uzs=total_debt
        )

        # 4. Ensure counterparty card in MoySklad has 'БЛОК' tag and warning
        await ms_client.update_counterparty_status(agent_id, "BLOCKED")

        # 5. Revoke 'applicable' (Проведено) in MoySklad
        unconduct_res = await ms_client.unconduct_demand(demand_id=demand_id)
        unconduct_success = unconduct_res.get("success", False)

        if unconduct_success:
            status_desc = "Ушбу мижозга Hard-Lock тақиқи қўйилган! Ҳужжат автоматик бекор қилинди (Проведено олиб ташланди)."
        else:
            status_desc = "Ушбу мижозга Hard-Lock тақиқи қўйилган! ⚠️ Ҳужжат дастур томонидан аниқланди, лекин МойСклад API ҳуқуқи чеклангани сабабли сотувни зудлик билан қўлда бекор қилинг!"

        # Format debt and sums with spaces (e.g. 1 537 882 283 сўм)
        debt_formatted = f"{int(round(total_debt)):,}".replace(",", " ")
        sum_formatted = f"{int(round(sum_uzs)):,}".replace(",", " ")

        # 6. Construct Urgent Telegram Alert
        tg_message = (
            f"🚨 <b>ДИҚҚАТ: ТАҚИҚЛАНГАН МИЖОЗГА СОТУВ ҚИЛИНДИ!</b>\n\n"
            f"👤 <b>Мижоз:</b> {company_title}\n"
            f"📄 <b>Ҳужжат:</b> Отгрузка № {doc_number}\n"
            f"💰 <b>Суммаси:</b> {sum_formatted} сўм\n"
            f"👨‍💼 <b>Сотувчи:</b> {owner_name}\n"
            f"⚠️ <b>Умумий қарзи:</b> {debt_formatted} сўм\n"
            f"🛑 <b>Ҳолат:</b> {status_desc}"
        )

        await telegram.send_alert(tg_message)

        # 5. Record Audit Event in local database
        try:
            async with async_session_factory() as session:
                audit = AuditEvent(
                    id=uuid4(),
                    event_type="HARD_LOCK_INTERCEPTED",
                    actor_id=owner_name or "moysklad_user",
                    payload={
                        "demand_id": demand_id,
                        "doc_number": doc_number,
                        "counterparty_id": agent_id,
                        "counterparty_name": company_title,
                        "amount_uzs": sum_uzs,
                        "owner": owner_name,
                        "unconduct_success": unconduct_success,
                        "action": action,
                        "timestamp": datetime.now().isoformat()
                    }
                )
                session.add(audit)
                await session.commit()
        except Exception as ex:
            logger.warning("failed_to_save_audit_event", error=str(ex))

        _ALREADY_INTERCEPTED.add(demand_id)

        return {
            "status": "blocked",
            "intercepted": True,
            "demand_id": demand_id,
            "doc_number": doc_number,
            "counterparty": company_title,
            "unconduct_success": unconduct_success,
            "notified_telegram": True
        }

    except Exception as e:
        logger.error("error_intercepting_demand", demand_id=demand_id, error=str(e))
        return {"status": "error", "demand_id": demand_id, "error": str(e)}
    finally:
        await ms_client.close()


@router.post("/moysklad-demand", summary="MoySklad Demand (Отгрузка) Webhook Handler")
@router.post("/demand", summary="MoySklad Demand Webhook Alias")
async def handle_moysklad_demand_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_ms_signature: str | None = Header(None, alias="X-Lognex-WebHook-Signature")
):
    """
    Receives MoySklad Webhook events for Demand (Отгрузка) entity.
    Identifies if counterparty is in Hard-Lock state, revokes conduction,
    and alerts management immediately via Telegram.
    """
    body_bytes = await request.body()

    # Optional signature check (only if secret is configured and header provided)
    if x_ms_signature and getattr(settings, "moysklad_webhook_secret", None):
        if not verify_moysklad_webhook(x_ms_signature, body_bytes):
            logger.warning("invalid_moysklad_webhook_signature")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")

    events = payload.get("events", [])
    if not events:
        # Check direct single event payload format
        if "meta" in payload and payload.get("meta", {}).get("type") == "demand":
            events = [payload]
        elif "demand_id" in payload:
            events = [{
                "meta": {"type": "demand", "href": f"https://api.moysklad.ru/api/remap/1.2/entity/demand/{payload['demand_id']}"},
                "action": payload.get("action", "CREATE")
            }]
        else:
            return {"status": "ignored", "reason": "No valid demand events in payload"}

    logger.info("moysklad_demand_webhook_received", event_count=len(events))

    results = []
    for event in events:
        meta = event.get("meta", {})
        entity_type = meta.get("type")
        href = meta.get("href", "")
        action = event.get("action", "UPDATE")

        if entity_type == "demand" and href:
            demand_id = href.split("/")[-1]
            if demand_id:
                # Process demand directly to intercept immediately
                res = await intercept_demand_document(demand_id=demand_id, action=action)
                results.append(res)

    return {
        "status": "processed",
        "events_count": len(events),
        "results": results
    }


@router.post("/check-recent-demands", summary="Scan and intercept recent demands for blocked counterparties")
async def check_recent_demands(limit: int = 20):
    """
    Scans the latest demand documents from MoySklad and verifies no conducted
    shipments exist for blocked counterparties. Useful as an automated fallback or manual audit.
    """
    ms_client = MoySkladClient()
    scanned = 0
    intercepted_count = 0
    intercepted_details = []

    try:
        res = await ms_client._request(
            "GET",
            "/entity/demand",
            params={
                "order": "moment,desc",
                "limit": min(limit, 100),
                "expand": "agent,owner"
            }
        )
        rows = res.get("rows", [])
        scanned = len(rows)

        # Get all blocked counterparty IDs from DB
        async with async_session_factory() as session:
            stmt = select(DebtRegistry.company_moysklad_id).where(DebtRegistry.is_blocked == True)
            blocked_res = await session.execute(stmt)
            blocked_ids = set(r[0] for r in blocked_res.fetchall() if r[0])

        for row in rows:
            demand_id = row.get("id")
            applicable = row.get("applicable", False)
            agent = row.get("agent", {}) or {}
            agent_id = agent.get("id")
            if not agent_id and "meta" in agent:
                agent_id = agent["meta"].get("href", "").split("/")[-1]

            # If agent is in blocked list and demand is applicable (conducted)
            if agent_id in blocked_ids and applicable:
                intercept_result = await intercept_demand_document(demand_id=demand_id, action="AUDIT_SCAN")
                if intercept_result.get("intercepted"):
                    intercepted_count += 1
                    intercepted_details.append(intercept_result)

    except Exception as e:
        logger.error("check_recent_demands_failed", error=str(e))
        return {"status": "error", "error": str(e)}
    finally:
        await ms_client.close()

    return {
        "status": "completed",
        "scanned_count": scanned,
        "intercepted_count": intercepted_count,
        "intercepted": intercepted_details
    }


@router.post("/register-demand-webhooks", summary="Register or inspect demand webhooks in MoySklad")
async def register_demand_webhooks(
    webhook_url: str = "https://diyorgroup.uz/api/v1/webhook/moysklad-demand"
):
    """
    Registers CREATE and UPDATE webhooks for 'demand' in MoySklad API.
    If current credentials lack administrator privileges, returns manual setup instructions.
    """
    ms_client = MoySkladClient()
    try:
        result = await ms_client.ensure_demand_webhooks(webhook_url=webhook_url)
        return {
            "result": result,
            "webhook_target_url": webhook_url,
            "manual_setup_instructions": {
                "description": "Агар МойСклад API фойдаланувчисига админ ҳуқуқи берилмаган бўлса, вебхукни МойСклад интерфейси орқали 1 дақиқада улаш мумкин:",
                "steps": [
                    "1. МойСклад кабинетига киринг -> Ўнг юқоридаги '⚙️ Настройки' (Созламалар) бўлимига ўтинг.",
                    "2. Чап менюдан 'Вебхуки' (ёки Аудит -> Вебхуки) бўлимини танланг.",
                    "3. '+ Вебхук' тугмасини босинг.",
                    f"4. URL манзилига қуйидагини киритинг: {webhook_url}",
                    "5. Тип сущности: 'Отгрузка' (demand) ни танланг.",
                    "6. Действие: 'Создание' (CREATE) ва 'Изменение' (UPDATE) ни танлаб, Сақлаш (Сохранить) тугмасини босинг."
                ]
            }
        }
    finally:
        await ms_client.close()
