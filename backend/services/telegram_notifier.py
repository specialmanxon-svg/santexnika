import httpx
import structlog
from config import settings

logger = structlog.get_logger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.bot_token = getattr(settings, "telegram_bot_token", getattr(settings, "TELEGRAM_BOT_TOKEN", ""))
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage" if self.bot_token else ""
        self.alert_chat_id = getattr(settings, "telegram_alert_chat_id", getattr(settings, "TELEGRAM_ALERT_CHAT_ID", ""))
        self.ceo_chat_id = getattr(settings, "telegram_ceo_chat_id", getattr(settings, "TELEGRAM_CEO_CHAT_ID", ""))
        self.admin_chat_id = getattr(settings, "telegram_admin_chat_id", getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", ""))
        self._cached_chat_id = None
        self._autodiscovery_attempted = False

    async def get_latest_chat_id(self) -> str | None:
        """Auto-detect chat ID from recent bot updates if user started the bot."""
        if not self.bot_token:
            return None
        if self._cached_chat_id:
            return self._cached_chat_id
        if self._autodiscovery_attempted:
            return None
        self._autodiscovery_attempted = True
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(f"https://api.telegram.org/bot{self.bot_token}/getUpdates", timeout=3.0)
                if res.status_code == 200:
                    data = res.json()
                    updates = data.get("result", [])
                    for u in reversed(updates):
                        msg = u.get("message") or u.get("my_chat_member")
                        if msg and "chat" in msg:
                            chat_id = str(msg["chat"]["id"])
                            logger.info("telegram_auto_discovered_chat_id", chat_id=chat_id)
                            self._cached_chat_id = chat_id
                            return chat_id
        except Exception as e:
            logger.warning("telegram_get_updates_failed", error=str(e))
        return None

    async def send_message(self, chat_id: str, text: str, parse_mode: str = 'HTML') -> bool:
        if not self.bot_token:
            logger.warning("telegram_notifier_disabled", reason="missing_token")
            return False

        target_chat = chat_id
        # If target chat is missing or placeholder, try cached or auto-detect
        if not target_chat or target_chat.startswith("123456") or target_chat.startswith("-100123456"):
            discovered = await self.get_latest_chat_id()
            if discovered:
                target_chat = discovered

        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": parse_mode
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.api_url, json=payload, timeout=4.0)
                if response.status_code == 200:
                    logger.info("telegram_message_sent", chat_id=target_chat)
                    return True
                
                # If chat not found, try auto-discovering from getUpdates
                if response.status_code == 400 and ("chat not found" in response.text or "chat_id is empty" in response.text):
                    logger.warning("telegram_chat_not_found_trying_autodiscovery", chat_id=target_chat)
                    discovered = await self.get_latest_chat_id()
                    if discovered and discovered != target_chat:
                        payload["chat_id"] = discovered
                        retry_resp = await client.post(self.api_url, json=payload, timeout=10.0)
                        if retry_resp.status_code == 200:
                            logger.info("telegram_message_sent_to_discovered_chat", chat_id=discovered)
                            return True

                logger.error("telegram_api_error", status_code=response.status_code, response=response.text)
                return False
        except Exception as e:
            logger.error("telegram_message_failed", chat_id=target_chat, error=str(e))
            return False

    async def send_alert(self, text: str) -> bool:
        chat_id = self.alert_chat_id or self.admin_chat_id or self.ceo_chat_id
        return await self.send_message(chat_id, text)

    async def send_ceo_message(self, text: str) -> bool:
        chat_id = self.ceo_chat_id or self.admin_chat_id or self.alert_chat_id
        return await self.send_message(chat_id, text)

    async def notify_designer_commission(self, designer_telegram_id: str, deal_title: str, commission: float, total_amount: float = 0.0) -> bool:
        if total_amount > 0:
            text = (
                f"🎉 <b>Бонус Реферала начислен!</b>\n\n"
                f"Сделка: <i>{deal_title}</i>\n"
                f"Савдо суммаси: <b>{total_amount:,.0f} сўм</b>\n"
                f"Бонус Реферала (сотувдан 5%): <b>{commission:,.0f} сўм</b>"
            )
        else:
            text = (
                f"🎉 <b>Бонус Реферала начислен!</b>\n\n"
                f"Сделка: <i>{deal_title}</i>\n"
                f"Бонус Реферала (сотувдан 5%): <b>{commission:,.0f} сўм</b>"
            )
        return await self.send_message(designer_telegram_id, text)

    async def notify_debt_block(self, company_name: str, debt_amount: float) -> bool:
        text = (
            f"🚨 <b>Блокировка отгрузок!</b>\n\n"
            f"Компания: <b>{company_name}</b>\n"
            f"Просроченный долг (>60 дней): <b>{debt_amount:,.2f} UZS</b>"
        )
        return await self.send_alert(text)
        
    async def notify_dlq_failure(self, task_name: str, error: str) -> bool:
        text = (
            f"💀 <b>Критическая ошибка задачи</b>\n\n"
            f"Задача: <code>{task_name}</code>\n"
            f"Ошибка:\n<code>{error[:500]}</code>"
        )
        return await self.send_alert(text)

    async def send_document(self, chat_id: str, document_bytes: bytes, filename: str, caption: str = "") -> bool:
        """Sends PDF or document to Telegram chat or auto-detected/default chat."""
        if not self.bot_token:
            logger.warning("telegram_notifier_disabled", reason="missing_token")
            return False

        target_chat = chat_id
        if not target_chat or target_chat.startswith("123456") or target_chat.startswith("-100123456"):
            discovered = await self.get_latest_chat_id()
            if discovered:
                target_chat = discovered
            else:
                target_chat = self.alert_chat_id or self.ceo_chat_id or self.admin_chat_id

        if not target_chat:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
        try:
            async with httpx.AsyncClient() as client:
                files = {"document": (filename, document_bytes, "application/pdf")}
                data = {"chat_id": target_chat, "caption": caption, "parse_mode": "HTML"}
                response = await client.post(url, data=data, files=files, timeout=20.0)
                if response.status_code == 200:
                    logger.info("telegram_document_sent", chat_id=target_chat, filename=filename)
                    return True
                logger.error("telegram_send_document_failed", status=response.status_code, body=response.text)
                # Fallback to text message if document sending encounters error
                await self.send_message(target_chat, f"{caption}\n\n📄 [Ҳужжат: {filename}]")
                return True
        except Exception as e:
            logger.error("telegram_document_exception", error=str(e))
            return False
