from datetime import datetime
from typing import Dict, Any, List

class GrowthDirectorAgent:
    """
    ИИ-Директор по развитию: Мониторинг строительных объектов и каскадное планирование.
    """
    def __init__(self):
        self.monitored_cities = ["Бухара", "Ташкент", "Алматы", "Астана"]
        self.premium_brands = ["Grohe", "Hansgrohe", "Geberit", "Vitra"]

    async def forecast_regional_demand(self, city: str) -> Dict[str, Any]:
        """Расчет вероятного спроса по сантехнике премиум-класса."""
        if city not in self.monitored_cities:
            raise ValueError(f"Город {city} не отслеживается. Доступные города: {self.monitored_cities}")

        base_demand_multiplier = {
            "Бухара": 1.0,
            "Ташкент": 3.5,
            "Алматы": 4.0,
            "Астана": 3.8
        }
        multiplier = base_demand_multiplier.get(city, 1.0)
        
        # Прогноз продаж
        forecast = {}
        for brand in self.premium_brands:
            forecast[brand] = {
                "expected_sales_volume_usd": round(15000 * multiplier, 2),
                "growth_potential_percent": round(15.5 * multiplier, 1),
                "trend": "upward"
            }

        return {
            "city": city,
            "forecast_date": datetime.now().isoformat(),
            "brand_forecast": forecast,
            "recommended_action": "Increase inventory for top performing brands"
        }

    async def decompose_annual_target(self, target_amount_uzs: float) -> Dict[str, Any]:
        """Разбивка годового таргета на спринты (Месячные, Недельные, Ежедневные)."""
        if target_amount_uzs <= 0:
            raise ValueError("Target amount must be greater than zero")

        monthly_target = target_amount_uzs / 12
        weekly_target = target_amount_uzs / 52
        daily_target = target_amount_uzs / 365

        seasonality = [0.8, 0.9, 1.1, 1.2, 1.3, 1.1, 1.0, 1.0, 1.2, 1.3, 1.1, 1.0]
        monthly_breakdown = []
        for i, factor in enumerate(seasonality):
            monthly_breakdown.append({
                "month": i + 1,
                "target_uzs": round(monthly_target * factor, 2)
            })

        return {
            "annual_target_uzs": target_amount_uzs,
            "monthly_average_target_uzs": round(monthly_target, 2),
            "weekly_average_target_uzs": round(weekly_target, 2),
            "daily_average_target_uzs": round(daily_target, 2),
            "monthly_breakdown": monthly_breakdown,
            "sprint_structure": "Annual -> 12 Months -> 52 Weeks -> 365 Days",
            "status": "Decomposed Successfully"
        }
