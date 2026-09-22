"""Referral Dispatcher Service for sending automated 5% cashback partnership offers via Telegram."""
import re
import urllib.parse
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from services.telegram_notifier import TelegramNotifier
from models.social import PartnerProfile
from models.audit import AuditEvent

logger = structlog.get_logger(__name__)

BOT_USERNAME = "Diyor_santexnika_2004_bot"

CYR_TO_LAT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    'ў': 'o', 'қ': 'q', 'ғ': 'g', 'ҳ': 'h'
}

def slugify_name(name: str) -> str:
    res = []
    for ch in name.lower():
        if ch in CYR_TO_LAT:
            res.append(CYR_TO_LAT[ch])
        elif ch.isalnum():
            res.append(ch)
        elif ch.isspace() or ch in ('_', '-'):
            res.append('_')
    slug = re.sub(r'_+', '_', ''.join(res)).strip('_')
    return slug[:12] or 'partner'


class ReferralDispatcherService:
    """
    Automated referral invitation service:
    Generates unique referral codes, formats dynamic personalized Uzbek invitation messages,
    dispatches notifications to Telegram bot & CEO channel, generates direct web Telegram chat links,
    and tracks conversion analytics for Tab 3 (Рассылки Telegram).
    """

    def __init__(self):
        self.notifier = TelegramNotifier()
        # In-memory store for fast lookup and stats synchronization
        self.offers: List[Dict[str, Any]] = [
            {
                "id": "OFFER-101",
                "specialist_id": "PLUMB-01",
                "name": "Ўктам Аҳмедов",
                "role": "Мастер-сантехник (Grohe Preferred)",
                "phone_or_tg": "+998 90 123 45 67",
                "region": "Бухара",
                "referral_code": "ref_uktam_bux",
                "referral_link": f"https://t.me/{BOT_USERNAME}?start=ref_uktam_bux",
                "status": "ACTIVE",
                "status_label": "В Рефералах (5%)",
                "delivered": True,
                "opened": True,
                "registered": True,
                "sent_at": "2026-09-15T10:30:00"
            },
            {
                "id": "OFFER-102",
                "specialist_id": "PLUMB-02",
                "name": "Жасур Тошев",
                "role": "Мастер-сантехник (Hansgrohe Expert)",
                "phone_or_tg": "+998 97 711 33 44",
                "region": "Ташкент",
                "referral_code": "ref_jasur_tosh",
                "referral_link": f"https://t.me/{BOT_USERNAME}?start=ref_jasur_tosh",
                "status": "ACTIVE",
                "status_label": "В Рефералах (5%)",
                "delivered": True,
                "opened": True,
                "registered": True,
                "sent_at": "2026-09-15T14:15:00"
            },
            {
                "id": "OFFER-103",
                "specialist_id": "ENT-01",
                "name": "Нодир Собиров (Nodir Interiors)",
                "role": "Дизайнер интерьера",
                "phone_or_tg": "@nodir_interiors",
                "region": "Бухара",
                "referral_code": "ref_nodir_int",
                "referral_link": f"https://t.me/{BOT_USERNAME}?start=ref_nodir_int",
                "status": "ACTIVE",
                "status_label": "В Рефералах (5%)",
                "delivered": True,
                "opened": True,
                "registered": True,
                "sent_at": "2026-09-16T09:45:00"
            },
            {
                "id": "OFFER-104",
                "specialist_id": "ENT-02",
                "name": "Шавкат Умаров",
                "role": "Прораб и Инженер",
                "phone_or_tg": "+998 93 456 78 90",
                "region": "Самарканд",
                "referral_code": "ref_shavkat_prorab",
                "referral_link": f"https://t.me/{BOT_USERNAME}?start=ref_shavkat_prorab",
                "status": "PENDING",
                "status_label": "Таклиф юборилди",
                "delivered": True,
                "opened": True,
                "registered": False,
                "sent_at": "2026-09-16T16:20:00"
            },
            {
                "id": "OFFER-105",
                "specialist_id": "ENT-03",
                "name": "Kamola Studio",
                "role": "Архитектурное бюро",
                "phone_or_tg": "@kamola_arch",
                "region": "Ташкент",
                "referral_code": "ref_kamola_arch",
                "referral_link": f"https://t.me/{BOT_USERNAME}?start=ref_kamola_arch",
                "status": "PENDING",
                "status_label": "Таклиф юборилди",
                "delivered": True,
                "opened": False,
                "registered": False,
                "sent_at": "2026-09-17T08:10:00"
            }
        ]

    def build_offer_message(self, name: str, referral_link: str, role: str = "") -> str:
        """
        Builds the standard dynamic Uzbek partnership invitation message.
        """
        lines = [
            f"Ассалому алайкум, {name}!",
            "Ишларингизни кузатиб борамиз, таъмирлаш ва қурилишдаги тажрибангиз юқори.",
            "",
            "«Diyor Group» компанияси сифатли усталар, прораблар ва дизайнерлар билан ҳамкорлик дастурини йўлга қўйди:",
            "",
            "• 5% нақд/картага бонус: Сизнинг тавсиянгиз ёки харидингиз билан мижоз биздан сантехника маҳсулотлари олса, умумий харид суммасининг 5% қисми шахсан сизга бонус сифатида тўлаб берилади.",
            "• Шахсий чегирмалар: Барча турдаги маҳсулотларга усталик улгуржи нархлари.",
            "• Кафолат ва етказиб бериш: Товарлар омборда мавжуд ва тўғридан-тўғри объектга етказилади.",
            "",
            "Ҳамкор сифатида рўйхатдан ўтиш ва рефераль ID рақамингизни олиш учун ушбу ҳаволани босинг:",
            f"👉 {referral_link}"
        ]
        return "\n".join(lines)

    def generate_direct_telegram_url(self, phone_or_tg: str, message_text: str, referral_link: str) -> str:
        """
        Generates direct Telegram url to open in browser/app so manager can 1-click send.
        """
        encoded_text = urllib.parse.quote(message_text)
        clean = phone_or_tg.strip()

        if clean.startswith("@"):
            username = clean.lstrip("@")
            return f"https://t.me/{username}?text={encoded_text}"
        
        # Check if digits only (phone number)
        digits = re.sub(r"[^\d]", "", clean)
        if len(digits) >= 9:
            return f"https://t.me/+{digits}?text={encoded_text}"
        
        # Fallback share link
        encoded_link = urllib.parse.quote(referral_link)
        return f"https://t.me/share/url?url={encoded_link}&text={encoded_text}"

    async def send_offer(
        self,
        name: str,
        phone_or_tg: str,
        role: str = "Мастер-сантехник",
        region: str = "Ташкент",
        specialist_id: Optional[str] = None,
        session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """
        Sends 5% referral offer, generates referral code, notifies Telegram and returns direct link.
        """
        # 1. Generate unique referral code and bot link
        clean_slug = slugify_name(name)
        unique_suffix = uuid.uuid4().hex[:6]
        referral_code = f"ref_{clean_slug}_{unique_suffix}"
        referral_link = f"https://t.me/{BOT_USERNAME}?start={referral_code}"

        # 2. Build personalized message
        message_text = self.build_offer_message(name=name, referral_link=referral_link, role=role)

        # 3. Build direct Telegram chat link
        direct_tg_url = self.generate_direct_telegram_url(phone_or_tg, message_text, referral_link)

        offer_id = f"OFFER-{len(self.offers) + 101}"

        # 4. Notify CEO / Sales team via Telegram Bot
        ceo_notify = (
            f"🤝 <b>5% Бонус дастури таклифномаси юборилди!</b>\n\n"
            f"👤 Мутахассис: <b>{name}</b>\n"
            f"🏷️ Профиль / Роль: <b>{role}</b>\n"
            f"📍 Ҳудуд: <b>{region}</b>\n"
            f"📞 Telegram / Тел: <code>{phone_or_tg}</code>\n"
            f"💰 Таклиф этилган бонус: <b>5% Кэшбэк</b>\n"
            f"🔗 Рефераль ҳавола: <code>{referral_link}</code>\n"
            f"⚡ Статус: <b>Таклиф юборилди (Кутилмоқда)</b>"
        )
        try:
            await self.notifier.send_ceo_message(ceo_notify)
        except Exception as e:
            logger.warning("telegram_offer_notify_failed", error=str(e))

        # 5. Record offer in memory registry
        new_offer = {
            "id": offer_id,
            "specialist_id": specialist_id or offer_id,
            "name": name,
            "role": role,
            "phone_or_tg": phone_or_tg,
            "region": region,
            "referral_code": referral_code,
            "referral_link": referral_link,
            "status": "PENDING",
            "status_label": "Таклиф юборилди",
            "delivered": True,
            "opened": False,
            "registered": False,
            "sent_at": datetime.now().isoformat(),
            "direct_tg_url": direct_tg_url
        }
        self.offers.insert(0, new_offer)

        # 6. If DB session provided, also log audit event and update partner profile
        if session:
            try:
                audit = AuditEvent(
                    id=uuid.uuid4(),
                    event_type="REFERRAL_OFFER_DISPATCHED",
                    actor_id="referral_dispatcher_agent",
                    payload={
                        "offer_id": offer_id,
                        "specialist_name": name,
                        "phone_or_tg": phone_or_tg,
                        "role": role,
                        "referral_code": referral_code,
                        "bonus": "5%"
                    }
                )
                session.add(audit)
                await session.commit()
            except Exception as e:
                logger.warning("db_audit_log_failed", error=str(e))

        logger.info("referral_offer_sent", name=name, code=referral_code, tg=phone_or_tg)

        return {
            "status": "success",
            "offer_id": offer_id,
            "specialist_id": specialist_id,
            "name": name,
            "role": role,
            "region": region,
            "phone_or_tg": phone_or_tg,
            "referral_code": referral_code,
            "referral_link": referral_link,
            "message_text": message_text,
            "direct_tg_url": direct_tg_url,
            "offer_status": "PENDING",
            "status_label": "Таклиф юборилди",
            "sent_at": new_offer["sent_at"]
        }

    async def confirm_offer(self, offer_id: str, session: Optional[AsyncSession] = None) -> Dict[str, Any]:
        """Marks an offer as verified partner in 5% cashback program."""
        found = None
        for off in self.offers:
            if off["id"] == offer_id or off["specialist_id"] == offer_id:
                off["status"] = "ACTIVE"
                off["status_label"] = "В Рефералах (5%)"
                off["registered"] = True
                off["opened"] = True
                found = off
                break
        
        if not found:
            return {"status": "not_found", "message": "Offer not found"}

        # Notify CEO of confirmation
        tg_text = (
            f"✅ <b>Мутахассис таклифномани қабул қилди!</b>\n\n"
            f"👤 Ҳамкор: <b>{found['name']}</b> ({found['role']})\n"
            f"📞 Алоқа: <code>{found['phone_or_tg']}</code>\n"
            f"💎 Мақом: <b>В Рефералах (5% Кэшбэк фаоллаштирилди)</b>\n"
            f"🔗 Рефераль код: <code>{found['referral_code']}</code>"
        )
        try:
            await self.notifier.send_ceo_message(tg_text)
        except Exception as e:
            logger.warning("confirm_notify_failed", error=str(e))

        return {
            "status": "success",
            "offer_id": found["id"],
            "name": found["name"],
            "offer_status": "ACTIVE",
            "status_label": "В Рефералах (5%)"
        }

    async def get_stats(self) -> Dict[str, Any]:
        """Calculates live stats for Tab 3 (Рассылки Telegram)."""
        total = len(self.offers)
        opened = sum(1 for o in self.offers if o.get("opened", False))
        bot_joined = sum(1 for o in self.offers if o.get("registered", False))
        active = sum(1 for o in self.offers if o.get("status") == "ACTIVE")

        return {
            "status": "success",
            "summary": {
                "total_sent": total,
                "opened_count": opened,
                "bot_joined_count": bot_joined,
                "active_partners_count": active,
                "open_rate_percent": round((opened / total * 100), 1) if total > 0 else 0,
                "conversion_percent": round((active / total * 100), 1) if total > 0 else 0
            },
            "recent_offers": self.offers[:10]
        }

referral_dispatcher = ReferralDispatcherService()
