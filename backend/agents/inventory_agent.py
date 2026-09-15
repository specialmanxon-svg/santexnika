from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

class InventoryAgent:
    """
    AI Inventory Agent: ABC/XYZ матрица оборачиваемости и аудит неликвидных товаров.
    """
    
    async def audit_inventory_liquidity(self, session: AsyncSession) -> Dict[str, Any]:
        """
        Проверяет товары на складе. 
        Если days_in_stock > 90 дней — маркирует is_non_liquid = True и 
        выставляет manager_bonus_percent = 5.0.
        """
        # Мок для примера
        mock_products = [
            {"id": 1, "sku": "GRO-101", "name": "Grohe Mixer", "days_in_stock": 45, "is_non_liquid": False, "manager_bonus_percent": 1.0},
            {"id": 2, "sku": "HAN-202", "name": "Hansgrohe Shower", "days_in_stock": 120, "is_non_liquid": False, "manager_bonus_percent": 1.0},
            {"id": 3, "sku": "GEB-303", "name": "Geberit Installation", "days_in_stock": 95, "is_non_liquid": False, "manager_bonus_percent": 1.0},
        ]
        
        audit_report = []
        updated_count = 0
        
        for product in mock_products:
            if product["days_in_stock"] > 90:
                product["is_non_liquid"] = True
                product["manager_bonus_percent"] = 5.0
                updated_count += 1
                
                # В реальной системе: session.add(product) и т.д.
                
                audit_report.append({
                    "sku": product["sku"],
                    "name": product["name"],
                    "days_in_stock": product["days_in_stock"],
                    "action_taken": "Marked as non-liquid, bonus increased to 5.0%"
                })
                
        # await session.commit()

        return {
            "audit_date": datetime.now().isoformat(),
            "total_items_checked": len(mock_products),
            "non_liquid_items_found": updated_count,
            "report": audit_report,
            "status": "Audit Completed"
        }
