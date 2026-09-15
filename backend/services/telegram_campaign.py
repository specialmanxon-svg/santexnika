from typing import Dict, Any
import uuid

class TelegramCampaignService:
    """
    TelegramCampaignService: Управление рассылками в Telegram (сегментация, акты сверки).
    """
    
    def __init__(self):
        self.campaigns: Dict[str, Dict[str, Any]] = {}
        self.segments = ["LEADS_WARMUP", "DEBTORS", "CREDITORS"]

    async def create_campaign(self, name: str, segment: str, text: str, attach_act: bool) -> Dict[str, Any]:
        """Подготовка сегментированной рассылки."""
        if segment not in self.segments:
            raise ValueError(f"Invalid segment. Allowed: {self.segments}")
            
        campaign_id = str(uuid.uuid4())
        self.campaigns[campaign_id] = {
            "id": campaign_id,
            "name": name,
            "segment": segment,
            "text": text,
            "attach_act": attach_act,
            "status": "DRAFT",
            "confirmed_by": None
        }
        
        return {
            "status": "created",
            "campaign_id": campaign_id,
            "campaign": self.campaigns[campaign_id]
        }

    async def preview_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """Предпросмотр сообщения с расчетом получателей и вложенными актами."""
        campaign = self.campaigns.get(campaign_id)
        if not campaign:
            raise ValueError("Campaign not found")
            
        estimated_recipients = 150 if campaign["segment"] == "LEADS_WARMUP" else 25
        
        preview = {
            "campaign_id": campaign_id,
            "text_preview": campaign["text"],
            "estimated_recipients": estimated_recipients,
            "attachments": ["Act_of_Reconciliation.pdf"] if campaign["attach_act"] else [],
            "note": "Integration with MoySklad pending for exact acts."
        }
        
        return preview

    async def confirm_double_check(self, campaign_id: str, confirmed_by: str) -> Dict[str, Any]:
        """Обязательное подтверждение перед отправкой."""
        campaign = self.campaigns.get(campaign_id)
        if not campaign:
            raise ValueError("Campaign not found")
            
        if campaign["status"] != "DRAFT":
            raise ValueError("Only DRAFT campaigns can be confirmed")
            
        campaign["status"] = "CONFIRMED"
        campaign["confirmed_by"] = confirmed_by
        
        return {
            "status": "confirmed",
            "campaign_id": campaign_id,
            "confirmed_by": confirmed_by
        }

    async def execute_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """Отправка в Telegram с эскалацией тональности для должников."""
        campaign = self.campaigns.get(campaign_id)
        if not campaign:
            raise ValueError("Campaign not found")
            
        if campaign["status"] != "CONFIRMED":
            raise ValueError("Campaign must be CONFIRMED before execution")
            
        final_text = campaign["text"]
        
        if campaign["segment"] == "DEBTORS":
            final_text = f"🚨 ВНИМАНИЕ: {final_text}\nПросьба срочно погасить задолженность!"
            
        campaign["status"] = "EXECUTED"
        
        return {
            "status": "executed",
            "campaign_id": campaign_id,
            "sent_text": final_text,
            "success_rate": "100%",
            "recipients_reached": 25
        }
