from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

class SocialIntelligenceService:
    """
    SocialIntelligenceService: Анализ соцсетей для поиска лидов и партнеров.
    """
    
    def __init__(self):
        self.partner_keywords = ["дизайнер интерьера", "архитектор", "interior designer", "arch", "дизайн студия"]
        self.intent_keywords = ["ремонт", "смеситель", "сантехника", "инсталляция"]

    async def detect_partner_lead(
        self, 
        instagram_username: str, 
        bio_text: str, 
        source_account: str, 
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Ищет дизайнеров/архитекторов и создает PartnerProfile с комиссией 7%."""
        
        bio_lower = bio_text.lower()
        is_partner = any(keyword in bio_lower for keyword in self.partner_keywords)
        
        if not is_partner:
            return {"status": "ignored", "reason": "No partner keywords found in bio"}
            
        partner_profile = {
            "username": instagram_username,
            "source": source_account,
            "commission_percent": 7.0,
            "type": "Designer/Architect"
        }
        
        bitrix_task = {
            "title": f"New Designer Lead: {instagram_username}",
            "attribution": "Designer Attribution",
            "action": "Contact for partnership"
        }
        
        return {
            "status": "partner_created",
            "profile": partner_profile,
            "crm_integration": bitrix_task
        }

    async def scan_story_intent(
        self, 
        username: str, 
        story_text: str, 
        story_url: str, 
        session: AsyncSession
    ) -> Dict[str, Any]:
        """Сканирует сторис на наличие маркеров ремонта для создания SocialStoryLead."""
        
        text_lower = story_text.lower()
        matched_keywords = [kw for kw in self.intent_keywords if kw in text_lower]
        
        if not matched_keywords:
            return {"status": "no_intent", "reason": "No intent keywords found"}
            
        story_lead = {
            "username": username,
            "story_url": story_url,
            "matched_keywords": matched_keywords,
            "intent_level": "High" if len(matched_keywords) > 1 else "Medium"
        }
        
        bitrix_task = {
            "title": f"Lead Warm-up: {username}",
            "description": f"User posted about {', '.join(matched_keywords)}",
            "task_type": "Lead Warm-up"
        }
        
        return {
            "status": "lead_created",
            "lead": story_lead,
            "crm_integration": bitrix_task
        }

    async def generate_plumber_kp(self, plumber_id: str, session: AsyncSession) -> Dict[str, Any]:
        """Формирует персонализированное КП для мастера (Grohe vs Geberit)."""
        
        plumber_data = {
            "id": plumber_id,
            "name": "Иван Мастеров",
            "competence_preference": "Grohe"
        }
        
        if plumber_data["competence_preference"] == "Grohe":
            kp_text = "Специальное предложение на инсталляции и смесители Grohe! Скидка 15% для сертифицированных мастеров. Техническая поддержка 24/7."
            products = ["Grohe Rapid SL", "Grohe Eurosmart"]
        else:
            kp_text = "Эксклюзивные условия на системы Geberit. Повышенный кэшбек 10% и бесплатная доставка на объекты."
            products = ["Geberit Duofix", "Geberit Sigma"]
            
        return {
            "plumber_name": plumber_data["name"],
            "kp_document": {
                "title": "Персональное Коммерческое Предложение",
                "body": kp_text,
                "recommended_products": products
            },
            "status": "generated"
        }
