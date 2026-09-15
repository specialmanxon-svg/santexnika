import httpx
import structlog
from config import settings

logger = structlog.get_logger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        self.alert_chat_id = settings.TELEGRAM_ALERT_CHAT_ID
        self.ceo_chat_id = settings.TELEGRAM_CEO_CHAT_ID

    async def send_message(self, chat_id: str, text: str, parse_mode: str = 'HTML') -> bool:
        if not self.bot_token or not chat_id:
            logger.warning("telegram_notifier_disabled", reason="missing_token_or_chat_id")
            return False

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.api_url, json=payload, timeout=10.0)
                response.raise_for_status()
                logger.info("telegram_message_sent", chat_id=chat_id)
                return True
        except Exception as e:
            logger.error("telegram_message_failed", chat_id=chat_id, error=str(e))
            return False

    async def send_alert(self, text: str) -> bool:
        return await self.send_message(self.alert_chat_id, text)

    async def send_ceo_message(self, text: str) -> bool:
        return await self.send_message(self.ceo_chat_id, text)

    async def notify_designer_commission(self, designer_telegram_id: str, deal_title: str, commission: float) -> bool:
        text = (
            f"🎉 <b>Начислена комиссия!</b>\n\n"
            f"Сделка: <i>{deal_title}</i>\n"
            f"Сумма комиссии: <b>{commission:,.2f}</b> сумов"
        )
        return await self.send_message(designer_telegram_id, text)

    async def notify_debt_block(self, company_name: str, debt_amount: float) -> bool:
        text = (
            f"🚨 <b>Блокировка отгрузок!</b>\n\n"
            f"Компания: <b>{company_name}</b>\n"
            f"Просроченный долг (>60 дней): <b>{debt_amount:,.2f}</b>"
        )
        return await self.send_alert(text)
        
    async def notify_dlq_failure(self, task_name: str, error: str) -> bool:
        text = (
            f"💀 <b>Критическая ошибка задачи</b>\n\n"
            f"Задача: <code>{task_name}</code>\n"
            f"Ошибка:\n<code>{error[:500]}</code>"
        )
        return await self.send_alert(text)
