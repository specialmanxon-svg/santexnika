from typing import Dict, Any, List

class ContentSMMAgent:
    """
    AI Content & SMM Agent: Мониторинг трендов, генерация сценариев Reels и промптов для Meta Ads.
    """
    def __init__(self):
        self.current_trends = [
            "скрытые термостаты",
            "матовый черный",
            "встраиваемые рамы",
            "подвесные унитазы"
        ]
        self.supported_brands = ["Vitra", "Hansgrohe", "Grohe"]

    async def generate_reels_script(self, brand: str, product_type: str) -> Dict[str, str]:
        """Генерация сценария Reels (Hook, Body, CTA)."""
        if brand not in self.supported_brands:
            raise ValueError(f"Brand {brand} not supported. Supported: {self.supported_brands}")

        trend = self.current_trends[0]
        
        return {
            "brand": brand,
            "product_type": product_type,
            "hook": f"Устали от обычного дизайна ванной? Посмотрите на {product_type} от {brand}! Тренд 2026 года: {trend}.",
            "body": f"Этот {product_type} отличается премиальным качеством и инновационными технологиями. {brand} гарантирует надежность и стиль. Идеально вписывается в современный интерьер.",
            "cta": "Сохрани это видео, чтобы не потерять идею для ремонта, и переходи в профиль за подробностями!"
        }

    async def generate_ad_prompts(self, brand: str) -> Dict[str, Any]:
        """Генерация промптов для Meta Ads таргетинга и анализ метрик."""
        prompts = [
            f"Show an elegant bathroom featuring {brand} fixtures in matte black finish, photorealistic, 4k",
            f"Minimalist modern bathroom interior with {brand} concealed thermostat, high quality architectural visualization",
            f"Luxury {brand} wall-hung toilet installation, cinematic lighting, interior design trend 2026"
        ]
        
        metrics = {
            "expected_ctr": "2.5% - 3.8%",
            "estimated_reach_per_100usd": "15,000 - 25,000",
            "target_audience": "Interior Designers, High-income homeowners, Architects"
        }
        
        return {
            "prompts": prompts,
            "metrics": metrics
        }
