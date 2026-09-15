from datetime import datetime
from typing import Dict, Any

class FieldHRAgent:
    """
    Полевой HR-Агент: Контроль посещаемости на объектах, интеграция с W3C Geolocation и фото EXIF.
    """
    def __init__(self):
        self.max_accuracy_meters = 20.0
        self.max_exif_delta_seconds = 180

    async def process_field_checkin(
        self, 
        employee_id: int, 
        employee_name: str, 
        object_name: str, 
        lat: float, 
        lon: float, 
        accuracy: float, 
        exif_delta: int, 
        is_mock: bool
    ) -> Dict[str, Any]:
        """Обработка чекина сотрудника на объекте."""
        
        status = "APPROVED"
        violations = []

        if accuracy > self.max_accuracy_meters:
            violations.append(f"Accuracy ({accuracy}m) exceeds limit ({self.max_accuracy_meters}m)")
            status = "REJECTED"
        
        if is_mock:
            violations.append("Mock location detected")
            status = "REJECTED"
            
        if exif_delta > self.max_exif_delta_seconds:
            violations.append(f"EXIF delta ({exif_delta}s) exceeds limit ({self.max_exif_delta_seconds}s)")
            status = "REJECTED"

        timesheet_entry = {
            "employee_id": employee_id,
            "employee_name": employee_name,
            "object_name": object_name,
            "checkin_time": datetime.now().isoformat(),
            "coordinates": {"lat": lat, "lon": lon},
            "accuracy": accuracy,
            "status": status,
            "violations": violations
        }

        # Saving to DB logic would go here
        
        return {
            "success": status == "APPROVED",
            "message": "Check-in processed",
            "entry": timesheet_entry
        }
