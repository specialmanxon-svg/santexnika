from typing import Any
import json

class RBACService:
    """
    RBACService: Управление доступом на основе ролей (Sales, Warehouse, Finance, Owner).
    """
    
    ROLES = ["sales", "warehouse", "finance", "owner"]
    
    PERMISSIONS = {
        "sales": {
            "finance": "status_only",
            "warehouse": "read_only",
            "deals": "edit_own",
            "clients": "read_write"
        },
        "warehouse": {
            "finance": "no_access",
            "warehouse": "full_access",
            "deals": "read_only",
            "clients": "no_access"
        },
        "finance": {
            "finance": "full_access",
            "warehouse": "read_only",
            "deals": "read_only",
            "clients": "read_only"
        },
        "owner": {
            "finance": "full_access",
            "warehouse": "full_access",
            "deals": "full_access",
            "clients": "full_access",
            "reports": "full_access"
        }
    }

    def check_access(self, role: str, module: str, required_level: str) -> bool:
        """Проверка прав доступа."""
        if role not in self.ROLES:
            return False
            
        if role == "owner":
            return True
            
        role_permissions = self.PERMISSIONS.get(role, {})
        actual_level = role_permissions.get(module, "no_access")
        
        levels = ["no_access", "status_only", "read_only", "edit_own", "read_write", "full_access"]
        try:
            return levels.index(actual_level) >= levels.index(required_level)
        except ValueError:
            return False

    async def export_data(self, format_type: str, dataset_name: str, requesting_role: str = "owner") -> bytes:
        """
        Экспорт в json, csv, excel для владельца.
        """
        if requesting_role != "owner":
            raise PermissionError("Only 'owner' role can export full data")
            
        supported_formats = ["json", "csv", "excel"]
        if format_type not in supported_formats:
            raise ValueError(f"Unsupported format: {format_type}")
            
        data = [
            {"id": 1, "dataset": dataset_name, "value": "Sample 1"},
            {"id": 2, "dataset": dataset_name, "value": "Sample 2"}
        ]
        
        if format_type == "json":
            result = json.dumps(data, ensure_ascii=False).encode('utf-8')
        elif format_type == "csv":
            result = "id,dataset,value\n1,{},Sample 1\n2,{},Sample 2".format(dataset_name, dataset_name).encode('utf-8')
        elif format_type == "excel":
            # Имитация бинарных данных Excel
            result = b"PK\x03\x04 dummy excel data"
        else:
            result = b""
            
        return result
