#!/usr/bin/env python3
"""Initialize Bitrix24 custom fields (UF_*) for deals and companies.

Run this script once to set up all required custom fields in Bitrix24 CRM.
Requires BITRIX24_DOMAIN and BITRIX24_WEBHOOK_SECRET environment variables.

Usage:
    python scripts/init_bitrix_fields.py
"""
import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from config import settings
import httpx


BASE_URL = f"{settings.bitrix24_domain}/rest/{settings.bitrix24_webhook_user_id}/{settings.bitrix24_webhook_secret}"


# Define all deal custom fields from TZ §2.1
DEAL_FIELDS = [
    {
        "FIELD_NAME": "UF_CRM_DEAL_TYPE",
        "USER_TYPE_ID": "enumeration",
        "EDIT_FORM_LABEL": {"ru": "Направление сделки", "en": "Deal Type"},
        "LIST_COLUMN_LABEL": {"ru": "Направление"},
        "MANDATORY": "N",
        "MULTIPLE": "N",
        "LIST": [
            {"VALUE": "B2C_SHOWROOM", "DEF": "Y", "SORT": 10},
            {"VALUE": "B2B_OBJECT", "DEF": "N", "SORT": 20}
        ]
    },
    {
        "FIELD_NAME": "UF_MS_ORDER_ID",
        "USER_TYPE_ID": "string",
        "EDIT_FORM_LABEL": {"ru": "МойСклад Order UUID", "en": "MoySklad Order UUID"},
        "MANDATORY": "N",
        "SETTINGS": {"SIZE": 64}
    },
    {
        "FIELD_NAME": "UF_DESIGNER_ID",
        "USER_TYPE_ID": "crm",
        "EDIT_FORM_LABEL": {"ru": "Привязка дизайнера", "en": "Designer Link"},
        "MANDATORY": "N",
        "SETTINGS": {"LEAD": "N", "CONTACT": "Y", "COMPANY": "Y", "DEAL": "N"}
    },
    {
        "FIELD_NAME": "UF_DESIGNER_COMMISSION",
        "USER_TYPE_ID": "double",
        "EDIT_FORM_LABEL": {"ru": "Комиссионные дизайнера", "en": "Designer Commission"},
        "MANDATORY": "N",
        "SETTINGS": {"PRECISION": 2}
    },
    {
        "FIELD_NAME": "UF_COMPAT_STATUS",
        "USER_TYPE_ID": "enumeration",
        "EDIT_FORM_LABEL": {"ru": "Статус совместимости", "en": "Compatibility Status"},
        "MANDATORY": "N",
        "MULTIPLE": "N",
        "LIST": [
            {"VALUE": "CHECK_PENDING", "DEF": "Y", "SORT": 10},
            {"VALUE": "COMPATIBLE", "DEF": "N", "SORT": 20},
            {"VALUE": "HUMAN_REVIEW_REQ", "DEF": "N", "SORT": 30}
        ]
    }
]

# Define all company custom fields from TZ §2.2
COMPANY_FIELDS = [
    {
        "FIELD_NAME": "UF_MS_COUNTERPARTY_ID",
        "USER_TYPE_ID": "string",
        "EDIT_FORM_LABEL": {"ru": "UUID МойСклад", "en": "MoySklad UUID"},
        "MANDATORY": "N",
        "SETTINGS": {"SIZE": 64}
    },
    {
        "FIELD_NAME": "UF_DEBT_TOTAL",
        "USER_TYPE_ID": "double",
        "EDIT_FORM_LABEL": {"ru": "Общая задолженность", "en": "Total Debt"},
        "MANDATORY": "N",
        "SETTINGS": {"PRECISION": 2}
    },
    {
        "FIELD_NAME": "UF_DEBT_OVERDUE_60",
        "USER_TYPE_ID": "double",
        "EDIT_FORM_LABEL": {"ru": "Просрочка > 60 дней", "en": "Overdue > 60 days"},
        "MANDATORY": "N",
        "SETTINGS": {"PRECISION": 2}
    },
    {
        "FIELD_NAME": "UF_SHIPMENT_BLOCKED",
        "USER_TYPE_ID": "boolean",
        "EDIT_FORM_LABEL": {"ru": "Блокировка отгрузок", "en": "Shipment Blocked"},
        "MANDATORY": "N"
    },
    {
        "FIELD_NAME": "UF_OVERRIDE_ACTIVE",
        "USER_TYPE_ID": "boolean",
        "EDIT_FORM_LABEL": {"ru": "Ручной допуск отгрузки", "en": "Override Active"},
        "MANDATORY": "N"
    }
]


async def create_field(client: httpx.AsyncClient, entity_type: str, field_def: dict) -> dict:
    """Create a single custom field in Bitrix24."""
    method = f"crm.{entity_type}.userfield.add"
    url = f"{BASE_URL}/{method}.json"
    
    try:
        response = await client.post(url, json={"fields": field_def})
        response.raise_for_status()
        result = response.json()
        
        if "result" in result:
            print(f"  ✅ {field_def['FIELD_NAME']} created (ID: {result['result']})")
        elif "error" in result:
            if "DUPLICATE" in str(result.get("error_description", "")).upper():
                print(f"  ⚠️  {field_def['FIELD_NAME']} already exists (skipped)")
            else:
                print(f"  ❌ {field_def['FIELD_NAME']} error: {result.get('error_description')}")
        return result
    except Exception as e:
        print(f"  ❌ {field_def['FIELD_NAME']} failed: {e}")
        return {"error": str(e)}


async def main():
    print("="*60)
    print("📦 Инициализация пользовательских полей Bitrix24")
    print(f"🌐 Домен: {settings.bitrix24_domain}")
    print("="*60)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Create deal fields
        print("\n📝 Создание полей сделок (CRM Deal):")
        for field in DEAL_FIELDS:
            await create_field(client, "deal", field)
            await asyncio.sleep(0.6)  # Rate limit: 2 req/sec
        
        # Create company fields
        print("\n🏢 Создание полей компаний (CRM Company):")
        for field in COMPANY_FIELDS:
            await create_field(client, "company", field)
            await asyncio.sleep(0.6)
    
    print("\n" + "="*60)
    print("✅ Инициализация завершена!")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
