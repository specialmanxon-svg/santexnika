#!/usr/bin/env python3
"""Seed the compatibility matrix with plumbing component relationships.

Populates the compatibility_matrix table with known compatible combinations
for major brands: Grohe, Hansgrohe, Vitra, Geberit.

Usage:
    python scripts/seed_compatibility.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.dialects.postgresql import insert
from config import settings
from models.base import Base
from models.compatibility import CompatibilityRule

# Compatibility data for premium plumbing brands
# Format: (base_sku, compatible_sku, component_type)
COMPATIBILITY_DATA = [
    # Geberit Duofix frames with flush plates
    ("GEBERIT-111.300", "GEBERIT-115.883", "flush_plate"),  # Duofix + Sigma 30
    ("GEBERIT-111.300", "GEBERIT-115.884", "flush_plate"),  # Duofix + Sigma 50
    ("GEBERIT-111.300", "GEBERIT-115.770", "flush_plate"),  # Duofix + Sigma 01
    ("GEBERIT-111.300", "GEBERIT-115.788", "flush_plate"),  # Duofix + Sigma 20
    ("GEBERIT-111.320", "GEBERIT-115.883", "flush_plate"),  # Duofix 112cm + Sigma 30
    ("GEBERIT-111.320", "GEBERIT-115.884", "flush_plate"),  # Duofix 112cm + Sigma 50
    
    # Grohe Rapid SL frames with flush plates
    ("GROHE-38528001", "GROHE-38505000", "flush_plate"),   # Rapid SL + Skate Air
    ("GROHE-38528001", "GROHE-38732000", "flush_plate"),   # Rapid SL + Skate Cosmopolitan  
    ("GROHE-38528001", "GROHE-38844000", "flush_plate"),   # Rapid SL + Arena Cosmopolitan
    ("GROHE-38536001", "GROHE-38505000", "flush_plate"),   # Rapid SL 3-in-1 + Skate Air
    
    # Hansgrohe iBox universal concealed bodies with trim sets
    ("HANSGROHE-01800180", "HANSGROHE-31666000", "trim_set"),  # iBox + Metropol
    ("HANSGROHE-01800180", "HANSGROHE-15407000", "trim_set"),  # iBox + PuraVida
    ("HANSGROHE-01800180", "HANSGROHE-31665000", "trim_set"),  # iBox + Metropol
    ("HANSGROHE-01800180", "HANSGROHE-32550000", "trim_set"),  # iBox + Metropol
    ("HANSGROHE-01800180", "HANSGROHE-15767000", "trim_set"),  # iBox + ShowerSelect
    
    # Grohe Rapido SmartBox concealed bodies
    ("GROHE-35600000", "GROHE-24063000", "trim_set"),    # SmartBox + Grohtherm SmartControl
    ("GROHE-35600000", "GROHE-29126000", "trim_set"),    # SmartBox + Grohtherm SmartControl round  
    ("GROHE-35600000", "GROHE-24157000", "trim_set"),    # SmartBox + Grohtherm SmartControl
    
    # Vitra installations with panels
    ("VITRA-742-5800-01", "VITRA-740-1780", "flush_plate"),  # Vitra frame + panel
    ("VITRA-742-5800-01", "VITRA-740-1781", "flush_plate"),  # Vitra frame + chrome panel
    ("VITRA-742-5800-01", "VITRA-740-1785", "flush_plate"),  # Vitra frame + glass panel
    
    # Cross-brand: Geberit frames work with some Grohe plates via adapter
    # These are NOT compatible (would need to be verified by engineer)
    
    # Shower system compatibility
    ("GROHE-26250000", "GROHE-35600000", "concealed_box"),  # Rainshower SmartActive + SmartBox
    ("HANSGROHE-27106000", "HANSGROHE-01800180", "concealed_box"),  # Raindance + iBox
]


async def main():
    print("="*60)
    print("🔧 Заполнение матрицы совместимости сантехники")
    print("="*60)
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    # Create tables if needed
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with async_session() as session:
        count = 0
        for base_sku, compatible_sku, component_type in COMPATIBILITY_DATA:
            stmt = insert(CompatibilityRule).values(
                base_sku=base_sku,
                compatible_sku=compatible_sku,
                component_type=component_type,
                is_verified=True
            ).on_conflict_do_nothing(
                constraint='unique_compat_pair'
            )
            result = await session.execute(stmt)
            if result.rowcount > 0:
                count += 1
                print(f"  ✅ {base_sku} ↔ {compatible_sku} ({component_type})")
            else:
                print(f"  ⚠️  {base_sku} ↔ {compatible_sku} (already exists)")
        
        await session.commit()
    
    await engine.dispose()
    
    print(f"\n✅ Добавлено {count} новых правил совместимости")
    print(f"   Всего правил в базе: {len(COMPATIBILITY_DATA)}")


if __name__ == "__main__":
    asyncio.run(main())
