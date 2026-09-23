import os
import json
import asyncio
import time
import math
import uuid
import structlog
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from config import settings
from models.hr import WorkTimesheet, Workplace
from services.moysklad_client import MoySkladClient
from services.telegram_notifier import TelegramNotifier

logger = structlog.get_logger(__name__)

# Uzbekistan Timezone (UTC+5)
UZ_TZ = timezone(timedelta(hours=5))


def sync_geofences_to_json(locations: List[Dict[str, Any]]) -> None:
    """Save geofences permanently to data/geofences.json file."""
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        root_dir = os.path.dirname(base_dir)
        data_dirs = [
            os.path.join(base_dir, "data"),
            os.path.join(root_dir, "data")
        ]
        for d in data_dirs:
            os.makedirs(d, exist_ok=True)
            filepath = os.path.join(d, "geofences.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(locations, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("sync_geofences_to_json_failed", error=str(e))


def load_geofences_from_json() -> List[Dict[str, Any]]:
    """Load geofences from data/geofences.json file if exists."""
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        root_dir = os.path.dirname(base_dir)
        paths = [
            os.path.join(base_dir, "data", "geofences.json"),
            os.path.join(root_dir, "data", "geofences.json")
        ]
        for p in paths:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and data:
                        return data
    except Exception as e:
        logger.warning("load_geofences_from_json_failed", error=str(e))
    return []



def calculate_haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate distance between two GPS points in meters using the Haversine formula.
    Strict parameter order: lat1, lon1, lat2, lon2.
    Auto-corrects swapped coordinates if latitude and longitude are inverted (Central Asia: Lat 37-45, Lon 56-73).
    """
    # Auto-detect and swap if lat and lon were inverted
    if lat1 > 50.0 and lon1 < 50.0:
        lat1, lon1 = lon1, lat1
    if lat2 > 50.0 and lon2 < 50.0:
        lat2, lon2 = lon2, lat2

    R = 6371000.0  # Earth's radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def format_money(val: float) -> str:
    """Format money value into Russian/Uzbek spaced format, e.g. 1 250 000 сўм."""
    try:
        return f"{int(round(val)):,}".replace(",", " ") + " сўм"
    except Exception:
        return f"{val} сўм"


_KPI_CACHE: Dict[str, Any] = {"ts": 0.0, "data": {}, "period": ""}


class HRService:
    def __init__(self):
        self.ms_client = MoySkladClient()
        self.telegram = TelegramNotifier()

    def get_store_location(self) -> Dict[str, Any]:
        """Return configured store/warehouse GPS coordinates and allowed radius."""
        return {
            "latitude": settings.store_latitude,
            "longitude": settings.store_longitude,
            "radius_meters": settings.store_radius_meters,
            "work_start_hour": settings.store_work_start_hour,
            "kpi_bonus_percent": settings.kpi_bonus_percent,
            "city": "Bukhara",
            "name": "Diyor Group — Марказий омбор ва савдо зали (Бухоро)"
        }

    async def get_locations(self, session: AsyncSession, active_only: bool = False) -> List[Dict[str, Any]]:
        """Get all registered workplaces and geofences, synced with data/geofences.json."""
        stmt = select(Workplace).order_by(Workplace.id)
        if active_only:
            stmt = stmt.where(Workplace.is_active == 1)
        res = await session.execute(stmt)
        locations = list(res.scalars().all())

        if not locations:
            # 1. Try restoring from data/geofences.json first
            from_json = load_geofences_from_json()
            if from_json:
                seeded_wps = []
                for item in from_json:
                    w = Workplace(
                        name=item.get("name", "Иш объекти"),
                        address=item.get("address"),
                        latitude=float(item.get("latitude", settings.STORE_LAT)),
                        longitude=float(item.get("longitude", settings.STORE_LON)),
                        radius_meters=float(item.get("radius_meters", 150.0)),
                        is_active=1 if item.get("is_active", True) else 0
                    )
                    seeded_wps.append(w)
                session.add_all(seeded_wps)
                await session.commit()
                for w in seeded_wps:
                    await session.refresh(w)
                locations = seeded_wps
            else:
                # 2. Auto-seed default workplaces if empty (Марказий дўкон ва Piramit Tower)
                default_wps = [
                    Workplace(
                        name="Марказий дўкон (Бухоро)",
                        address="Бухоро ш., Ибн Сино кўчаси",
                        latitude=settings.STORE_LAT,
                        longitude=settings.STORE_LON,
                        radius_meters=settings.MAX_DISTANCE_METERS,
                        is_active=1
                    ),
                    Workplace(
                        name="Piramit Tower (Тошкент, Бобур кўчаси)",
                        address="Тошкент ш., Яккасарой т., Бобур кўчаси, 44B",
                        latitude=41.281213,
                        longitude=69.254539,
                        radius_meters=300.0,
                        is_active=1
                    ),
                ]
                session.add_all(default_wps)
                await session.commit()
                for w in default_wps:
                    await session.refresh(w)
                locations = default_wps

        result = [
            {
                "id": loc.id,
                "name": loc.name,
                "address": loc.address or "",
                "latitude": loc.latitude,
                "longitude": loc.longitude,
                "radius_meters": loc.radius_meters,
                "is_active": bool(loc.is_active),
                "created_at": loc.created_at.strftime("%d.%m.%Y %H:%M") if loc.created_at else ""
            }
            for loc in locations
        ]

        # Sync full list to data/geofences.json
        if not active_only:
            sync_geofences_to_json(result)

        return result

    async def create_location(self, session: AsyncSession, data: Dict[str, Any]) -> Dict[str, Any]:
        """Register a new workplace/geofence and sync with data/geofences.json."""
        name = data.get("name")
        if not name or not str(name).strip():
            raise ValueError("Объект номи киритилиши шарт!")
        lat = data.get("latitude")
        lon = data.get("longitude")
        if lat is None or lon is None:
            raise ValueError("Координаталар (latitude ва longitude) киритилиши шарт!")
        
        # Auto-detect and swap if lat and lon were inverted
        lat_f = float(lat)
        lon_f = float(lon)
        if lat_f > 50.0 and lon_f < 50.0:
            lat_f, lon_f = lon_f, lat_f

        radius = float(data.get("radius_meters", 150.0))
        if radius <= 0:
            raise ValueError("Радиус 0 дан катта бўлиши шарт!")

        wp = Workplace(
            name=str(name).strip(),
            address=str(data.get("address", "")).strip() if data.get("address") else None,
            latitude=lat_f,
            longitude=lon_f,
            radius_meters=radius,
            is_active=1 if data.get("is_active", True) else 0
        )
        session.add(wp)
        await session.commit()
        await session.refresh(wp)

        # Sync updated list to geofences.json
        await self.get_locations(session, active_only=False)

        return {
            "status": "success",
            "message": f"«{wp.name}» объекти муваффақиятли қўшилди!",
            "location": {
                "id": wp.id,
                "name": wp.name,
                "address": wp.address or "",
                "latitude": wp.latitude,
                "longitude": wp.longitude,
                "radius_meters": wp.radius_meters,
                "is_active": bool(wp.is_active),
                "created_at": wp.created_at.strftime("%d.%m.%Y %H:%M") if wp.created_at else ""
            }
        }

    async def update_location(self, session: AsyncSession, location_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update workplace/geofence details and sync with data/geofences.json."""
        stmt = select(Workplace).where(Workplace.id == location_id)
        res = await session.execute(stmt)
        wp = res.scalar_one_or_none()
        if not wp:
            # If not in DB, create new location cleanly without throwing 404
            logger.info("workplace_not_found_on_update_creating_new", location_id=location_id)
            return await self.create_location(session, data)

        if "name" in data and data["name"] is not None:
            wp.name = str(data["name"]).strip()
        if "address" in data:
            wp.address = str(data["address"]).strip() if data["address"] else None
        
        lat = data.get("latitude")
        lon = data.get("longitude")
        if lat is not None and lon is not None:
            lat_f = float(lat)
            lon_f = float(lon)
            if lat_f > 50.0 and lon_f < 50.0:
                lat_f, lon_f = lon_f, lat_f
            wp.latitude = lat_f
            wp.longitude = lon_f
        elif lat is not None:
            wp.latitude = float(lat)
        elif lon is not None:
            wp.longitude = float(lon)

        if "radius_meters" in data and data["radius_meters"] is not None:
            wp.radius_meters = float(data["radius_meters"])
        if "is_active" in data and data["is_active"] is not None:
            wp.is_active = 1 if data["is_active"] else 0

        wp.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(wp)

        # Sync updated list to geofences.json
        await self.get_locations(session, active_only=False)

        return {
            "status": "success",
            "message": f"«{wp.name}» объекти муваффақиятли янгиланди!",
            "location": {
                "id": wp.id,
                "name": wp.name,
                "address": wp.address or "",
                "latitude": wp.latitude,
                "longitude": wp.longitude,
                "radius_meters": wp.radius_meters,
                "is_active": bool(wp.is_active)
            }
        }

    async def delete_location(self, session: AsyncSession, location_id: int, soft_delete: bool = False) -> Dict[str, Any]:
        """Delete or archive a workplace and sync with data/geofences.json."""
        stmt = select(Workplace).where(Workplace.id == location_id)
        res = await session.execute(stmt)
        wp = res.scalar_one_or_none()
        if not wp:
            # If not in DB, remove from geofences.json if present and return success
            current_json = load_geofences_from_json()
            filtered_json = [x for x in current_json if x.get("id") != location_id]
            sync_geofences_to_json(filtered_json)
            return {"status": "success", "message": f"Объект муваффақиятли ўчирилди (ID: {location_id})."}

        loc_name = wp.name
        if soft_delete:
            wp.is_active = 0
            wp.updated_at = datetime.utcnow()
            await session.commit()
            await self.get_locations(session, active_only=False)
            return {"status": "success", "message": f"«{loc_name}» объекти архивланди (нофаол қилинди)."}
        else:
            await session.delete(wp)
            await session.commit()
            await self.get_locations(session, active_only=False)
            return {"status": "success", "message": f"«{loc_name}» объекти ўчирилди."}

    async def checkin(
        self,
        session: AsyncSession,
        employee_id: int,
        employee_name: str,
        latitude: float = 0.0,
        longitude: float = 0.0,
        device_info: Optional[str] = None,
        override_object_name: Optional[str] = None,
        override_distance: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Check-in employee with multi-geofence GPS validation (Haversine formula).
        Verifies employee against all active workplaces/construction sites.
        """
        if not latitude or not longitude or (latitude == 0.0 and longitude == 0.0):
            raise ValueError("Геолокация координаталари аниқланмади. GPS рухсати ёқилганлигини текширинг!")

        # Auto-detect and swap if lat and lon were inverted (Central Asia: Lat 37-45, Lon 56-73)
        if latitude > 50.0 and longitude < 50.0:
            latitude, longitude = longitude, latitude

        # Load all active workplaces from database
        stmt = select(Workplace).where(Workplace.is_active == 1)
        result = await session.execute(stmt)
        workplaces = list(result.scalars().all())

        if not workplaces:
            # Fallback to configured central store
            workplaces = [
                Workplace(
                    id=1,
                    name="Асосий дўкон ва марказий омбор (Бухоро)",
                    address="Бухоро ш., Ибн Сино кўчаси",
                    latitude=settings.STORE_LAT,
                    longitude=settings.STORE_LON,
                    radius_meters=settings.MAX_DISTANCE_METERS,
                    is_active=1
                )
            ]

        # Calculate distances to all workplaces
        matched_workplaces = []
        all_distances = []
        for wp in workplaces:
            dist = calculate_haversine_distance(latitude, longitude, wp.latitude, wp.longitude)
            all_distances.append((wp, dist))
            if dist <= wp.radius_meters:
                matched_workplaces.append((wp, dist))

        # Check if matched any workplace or override provided by verified bot
        if not matched_workplaces:
            if override_object_name:
                matched_wp_name = override_object_name
                distance_rounded = round(override_distance or 0.0, 1)
                radius_info = 150
            else:
                closest_wp, closest_dist = min(all_distances, key=lambda x: x[1])
                closest_dist_rounded = round(closest_dist, 1)
                logger.warning(
                    "checkin_rejected_no_matching_geofence",
                    employee=employee_name,
                    closest_workplace=closest_wp.name,
                    distance=closest_dist_rounded,
                    allowed_radius=closest_wp.radius_meters
                )
                raise ValueError(
                    f"Ҳеч бир ишчи объект ҳудудида эмассиз (Энг яқин объект: {closest_wp.name}, масофа: {int(closest_dist_rounded)} метр. Рухсат этилган радиус: {int(closest_wp.radius_meters)} метр)"
                )
        else:
            # Pick matched workplace with minimum distance
            matched_wp, matched_dist = min(matched_workplaces, key=lambda x: x[1])
            matched_wp_name = override_object_name or matched_wp.name
            distance_rounded = round(matched_dist, 1)
            radius_info = int(matched_wp.radius_meters)

        # Determine punctuality based on local Bukhara time (UTC+5)
        now_local = datetime.now(UZ_TZ)
        now_utc = datetime.utcnow()
        start_hour = settings.store_work_start_hour

        if now_local.hour < start_hour or (now_local.hour == start_hour and now_local.minute <= 15):
            attendance_status = f"Ўз вақтида (Объект: {matched_wp_name})"
            status_code = "ON_TIME"
        else:
            late_mins = (now_local.hour - start_hour) * 60 + now_local.minute
            attendance_status = f"Кечикди ({late_mins} дақ) (Объект: {matched_wp_name})"
            status_code = "LATE"

        dev_label = "📱 Telegram" if (device_info and "telegram" in device_info.lower()) else "🌐 Web"

        ts = WorkTimesheet(
            employee_id=employee_id,
            employee_name=employee_name,
            object_name=matched_wp_name,
            checkin_time=now_utc,
            latitude=latitude,
            longitude=longitude,
            gps_accuracy=10.0,
            is_spoof_verified=1,
            exif_time_delta_sec=0,
            distance_meters=distance_rounded,
            attendance_status=attendance_status,
            device_info=dev_label,
            status="CHECKED_IN"
        )
        session.add(ts)
        await session.commit()
        await session.refresh(ts)

        # Notify CEO via Telegram
        msg = (
            f"📍 <b>Ишга келиш қайд этилди (GPS)</b>\n"
            f"👤 <b>Ходим:</b> {employee_name} (ID: {employee_id})\n"
            f"🏢 <b>Объект:</b> {matched_wp.name}\n"
            f"⏰ <b>Вақти:</b> {now_local.strftime('%d.%m.%Y %H:%M')}\n"
            f"📏 <b>Масофа:</b> {distance_rounded} метр (Радиус: {int(matched_wp.radius_meters)}м)\n"
            f"📊 <b>Ҳолат:</b> {attendance_status}\n"
            f"🗺 <a href='https://maps.google.com/?q={latitude},{longitude}'>Google Харитада кўриш</a>"
        )
        try:
            await self.telegram.send_ceo_message(msg)
        except Exception as te:
            logger.warning("telegram_notify_failed", error=str(te))

        return {
            "status": "success",
            "message": f"Ишга келиш муваффақиятли тасдиқланди! Объект: {matched_wp.name}",
            "object_name": matched_wp.name,
            "matched_location_id": matched_wp.id,
            "timesheet_id": str(ts.id),
            "checkin_time": now_local.strftime("%d.%m.%Y %H:%M"),
            "distance_meters": distance_rounded,
            "attendance_status": attendance_status,
            "status_code": status_code,
            "coordinates": {"lat": latitude, "lon": longitude}
        }


    async def checkout(
        self,
        session: AsyncSession,
        timesheet_id: Optional[str] = None,
        employee_id: Optional[int] = None,
        device_info: Optional[str] = None
    ) -> Dict[str, Any]:
        """Check-out for an employee and calculate worked hours."""
        ts = None
        if timesheet_id:
            try:
                clean_id = str(timesheet_id).strip().replace("'", "").replace('"', "")
                parsed_uuid = uuid.UUID(clean_id)
                stmt = select(WorkTimesheet).where(WorkTimesheet.id == parsed_uuid)
                result = await session.execute(stmt)
                ts = result.scalars().first()
            except Exception:
                stmt = select(WorkTimesheet).where(WorkTimesheet.id == timesheet_id)
                result = await session.execute(stmt)
                ts = result.scalars().first()

        if not ts and employee_id:
            stmt = select(WorkTimesheet).where(
                WorkTimesheet.employee_id == employee_id,
                WorkTimesheet.status == "CHECKED_IN"
            ).order_by(desc(WorkTimesheet.checkin_time))
            result = await session.execute(stmt)
            ts = result.scalars().first()

        if not ts:
            raise ValueError("Фаол давомад ёзуви топилмади ёки ходим аввалроқ ишдан чиқиб кетган.")

        now_utc = datetime.utcnow()
        ts.checkout_time = now_utc
        delta = ts.checkout_time - ts.checkin_time
        total_hours = max(0.0, delta.total_seconds() / 3600.0)
        ts.total_hours = round(total_hours, 2)
        ts.status = "CHECKED_OUT"
        ts.attendance_status = "Иш якунланди"
        if device_info:
            ts.device_info = device_info

        await session.commit()
        await session.refresh(ts)

        now_local = datetime.now(UZ_TZ)
        msg = (
            f"🏁 <b>Ишдан кетиш қайд этилди</b>\n"
            f"👤 <b>Ходим:</b> {ts.employee_name}\n"
            f"⏰ <b>Кетган вақти:</b> {now_local.strftime('%d.%m.%Y %H:%M')}\n"
            f"⏱ <b>Ишланган вақт:</b> {ts.total_hours:.2f} соат"
        )
        try:
            await self.telegram.send_ceo_message(msg)
        except Exception as te:
            logger.warning("telegram_notify_failed", error=str(te))

        return {
            "status": "success",
            "message": f"Ишдан кетиш қайд этилди. Жами ишланган вақт: {ts.total_hours:.2f} соат",
            "timesheet_id": str(ts.id),
            "total_hours": ts.total_hours,
            "checkout_time": now_local.strftime("%d.%m.%Y %H:%M")
        }

    async def get_timesheets(
        self,
        session: AsyncSession,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        employee_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get formatted timesheet records."""
        stmt = select(WorkTimesheet).order_by(desc(WorkTimesheet.checkin_time))
        if employee_id:
            stmt = stmt.where(WorkTimesheet.employee_id == employee_id)

        if date_from:
            try:
                dt_from = datetime.strptime(date_from, "%Y-%m-%d")
                stmt = stmt.where(WorkTimesheet.checkin_time >= dt_from)
            except ValueError:
                pass

        if date_to:
            try:
                dt_to = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
                stmt = stmt.where(WorkTimesheet.checkin_time <= dt_to)
            except ValueError:
                pass

        result = await session.execute(stmt)
        records = result.scalars().all()

        formatted = []
        for r in records:
            # Convert to local time UTC+5
            c_in_local = r.checkin_time + timedelta(hours=5) if r.checkin_time else None
            c_out_local = r.checkout_time + timedelta(hours=5) if r.checkout_time else None

            # Map link if coordinates exist
            map_url = ""
            if r.latitude and r.longitude:
                map_url = f"https://www.google.com/maps?q={r.latitude},{r.longitude}"

            formatted.append({
                "id": str(r.id),
                "employee_id": r.employee_id,
                "employee_name": r.employee_name,
                "object_name": r.object_name or "Марказий дўкон (Бухоро)",
                "checkin_time": c_in_local.strftime("%d.%m.%Y %H:%M") if c_in_local else "—",
                "checkout_time": c_out_local.strftime("%d.%m.%Y %H:%M") if c_out_local else "—",
                "total_hours": r.total_hours if r.checkout_time else "Ишланмоқда",
                "total_hours_num": r.total_hours or 0.0,
                "status": r.attendance_status or ("Ишда" if r.status == "CHECKED_IN" else "Якунланди"),
                "status_code": "ON_TIME" if "Ўз вақтида" in (r.attendance_status or "") else ("LATE" if "Кечикди" in (r.attendance_status or "") else "CHECKED_OUT"),
                "raw_status": r.status,
                "distance_meters": r.distance_meters,
                "device_info": "📱 Telegram" if (r.device_info and "telegram" in r.device_info.lower()) else "🌐 Web",
                "source": "Telegram" if (r.device_info and "telegram" in r.device_info.lower()) else "Web",
                "map_url": map_url
            })

        return formatted

    async def get_sales_kpi(self, period: str = "monthly", from_date: Optional[str] = None, to_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Calculate sales volume, deals count, and KPI bonuses for salespersons from MoySklad.
        Filters by current month or recent period for fast execution.
        Cached for 60 seconds.
        """
        cache_key = f"{period}_{from_date}_{to_date}"
        now_ts = time.time()
        if _KPI_CACHE.get("key") == cache_key and (now_ts - _KPI_CACHE.get("ts", 0.0) < 60.0) and _KPI_CACHE.get("data"):
            return _KPI_CACHE["data"]

        now = datetime.now()
        filters = []
        if from_date:
            filters.append(f"moment>={from_date} 00:00:00")
            if to_date:
                filters.append(f"moment<={to_date} 23:59:59")
            period_label = f"{from_date} — {to_date or now.strftime('%Y-%m-%d')}"
        elif period == "weekly":
            start_dt = now - timedelta(days=7)
            filters.append(f"moment>={start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            period_label = "Ҳафталик"
        elif period == "today":
            start_dt = datetime(now.year, now.month, now.day, 0, 0, 0)
            filters.append(f"moment>={start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            period_label = "Бугунги"
        else:
            # Current month by default
            start_dt = datetime(now.year, now.month, 1, 0, 0, 0)
            filters.append(f"moment>={start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            period_label = f"{now.strftime('%B %Y')} (Жорий ой)"

        filter_str = ";".join(filters)
        sales_map: Dict[str, Dict[str, Any]] = {}

        try:
            # MoySklad only expands linked entities (expand=owner) when limit <= 100!
            async def fetch_expanded(endpoint: str) -> list:
                rows = []
                for offset in (0, 100, 200):
                    try:
                        res = await self.ms_client._request(
                            "GET",
                            endpoint,
                            params={"filter": filter_str, "limit": 100, "offset": offset, "expand": "owner"}
                        )
                        r = res.get("rows", [])
                        rows.extend(r)
                        if len(r) < 100:
                            break
                    except Exception as fe:
                        logger.warning("moysklad_fetch_page_failed", endpoint=endpoint, offset=offset, error=str(fe))
                        break
                return rows

            d_rows, o_rows = await asyncio.gather(
                fetch_expanded("/entity/demand"),
                fetch_expanded("/entity/customerorder"),
                return_exceptions=True
            )

            # 1. Aggregate Demands (отгрузкалар / сотувлар)
            if isinstance(d_rows, list):
                for d in d_rows:
                    ow = d.get("owner")
                    owner_name = ow.get("name", "").strip() if isinstance(ow, dict) else ""
                    if not owner_name:
                        owner_name = "Умумий савдолар"
                    amount = float(d.get("sum", 0.0)) / 100.0

                    if owner_name not in sales_map:
                        sales_map[owner_name] = {"total_sales": 0.0, "deals_count": 0}
                    sales_map[owner_name]["total_sales"] += amount
                    sales_map[owner_name]["deals_count"] += 1

            # 2. Aggregate CustomerOrders (буюртмалар) if any additional salespersons exist
            if isinstance(o_rows, list):
                for o in o_rows:
                    ow = o.get("owner")
                    owner_name = ow.get("name", "").strip() if isinstance(ow, dict) else ""
                    if owner_name and owner_name != "Умумий савдолар":
                        amount = float(o.get("sum", 0.0)) / 100.0
                        # Only add if not already counted in demands or update orders count
                        if owner_name not in sales_map:
                            sales_map[owner_name] = {"total_sales": amount, "deals_count": 1}

        except Exception as exc:
            logger.error("moysklad_kpi_fetch_error", error=str(exc))

        # Check overdue debtors (60+ days) per salesperson for KPI Bonus Freeze
        seller_overdue_debtors: Dict[str, list] = {}
        try:
            reports = await self.ms_client.get_all_counterparty_reports()
            details_map = await self.ms_client.get_counterparty_details_map()
            demand_sellers = await self.ms_client.get_latest_demand_sellers()

            now_dt = datetime.utcnow()
            for r in reports:
                bal = float(r.get("balance", 0.0))
                if bal >= 0:
                    continue
                debt_val = abs(bal) / 100.0

                last_demand_str = r.get("lastDemandDate") or r.get("updated")
                days_overdue = 0
                if last_demand_str:
                    try:
                        clean_str = str(last_demand_str)[:19].replace("T", " ")
                        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
                        days_overdue = max(0, (now_dt - dt).days)
                    except Exception:
                        pass

                # If overdue > 60 days
                if days_overdue > 60 and debt_val > 0:
                    cp = r.get("counterparty", {})
                    cid = cp.get("id") or (cp.get("meta", {}).get("href", "").split("/")[-1].split("?")[0] if "meta" in cp else "")
                    cname = cp.get("name") or "Контрагент"
                    s_entry = demand_sellers.get(cid) or {}
                    s_name = s_entry.get("seller_name") or details_map.get(cid, {}).get("seller_name") or ""
                    if s_name and s_name != "Тайинланмаган":
                        if s_name not in seller_overdue_debtors:
                            seller_overdue_debtors[s_name] = []
                        seller_overdue_debtors[s_name].append({
                            "client": cname,
                            "debt": debt_val,
                            "days": days_overdue
                        })
        except Exception as deb_err:
            logger.warning("failed_to_check_seller_overdue_debtors", error=str(deb_err))

        # Calculate bonus and check for KPI Bonus Freeze
        bonus_pct = settings.kpi_bonus_percent
        sorted_kpi = []

        for name, data in sales_map.items():
            tot_sales = data["total_sales"]
            deals = data["deals_count"]
            bonus = tot_sales * (bonus_pct / 100.0)

            # Check if this seller has any overdue 60+ days debtors
            blocked_debtors = []
            clean_name = name.lower().replace(".", "").replace(" ", "")
            for s_name, dlist in seller_overdue_debtors.items():
                clean_s = s_name.lower().replace(".", "").replace(" ", "")
                if clean_s in clean_name or clean_name in clean_s or any(p in clean_name for p in s_name.lower().split() if len(p) > 2):
                    blocked_debtors.extend(dlist)

            is_bonus_blocked = len(blocked_debtors) > 0
            if is_bonus_blocked:
                bonus_status = "⚠️ Қарздорлик туфайли музлатилди"
                payable_bonus = 0.0
                blocked_info = ", ".join([f"{d['client']} ({int(round(d['debt'])):,} сўм)".replace(",", " ") for d in blocked_debtors[:3]])
            else:
                bonus_status = "Фаол"
                payable_bonus = bonus
                blocked_info = ""

            sorted_kpi.append({
                "salesperson": name,
                "name": name,
                "total_sales": round(tot_sales, 2),
                "formatted_sales": format_money(tot_sales),
                "deals_count": deals,
                "bonus": round(bonus, 2),
                "formatted_bonus": format_money(bonus),
                "payable_bonus": round(payable_bonus, 2),
                "formatted_payable_bonus": format_money(payable_bonus),
                "bonus_rate": f"{bonus_pct}%",
                "is_bonus_blocked": is_bonus_blocked,
                "bonus_status": bonus_status,
                "blocked_debtors": blocked_debtors,
                "blocked_debtors_info": blocked_info
            })

        sorted_kpi.sort(key=lambda x: x["total_sales"], reverse=True)

        total_calculated_bonus = sum(k["bonus"] for k in sorted_kpi)
        total_payable_bonus = sum(k["payable_bonus"] for k in sorted_kpi)
        frozen_bonus = total_calculated_bonus - total_payable_bonus

        res_data = {
            "period": period,
            "period_label": period_label,
            "bonus_percent": bonus_pct,
            "total_sales": sum(k["total_sales"] for k in sorted_kpi),
            "formatted_total_sales": format_money(sum(k["total_sales"] for k in sorted_kpi)),
            "total_bonus": total_calculated_bonus,
            "formatted_total_bonus": format_money(total_calculated_bonus),
            "total_payable_bonus": total_payable_bonus,
            "formatted_total_payable_bonus": format_money(total_payable_bonus),
            "frozen_bonus": frozen_bonus,
            "formatted_frozen_bonus": format_money(frozen_bonus),
            "kpi": sorted_kpi,
            "salespeople": sorted_kpi
        }

        _KPI_CACHE["ts"] = now_ts
        _KPI_CACHE["data"] = res_data
        _KPI_CACHE["key"] = cache_key
        _KPI_CACHE["period"] = period

        return res_data

    async def get_overview(
        self,
        session: AsyncSession,
        period: str = "monthly",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Unified overview endpoint returning attendance list, sales KPI, and store coordinates.
        """
        attendance_list = await self.get_timesheets(session, date_from=from_date, date_to=to_date)
        kpi_data = await self.get_sales_kpi(period, from_date=from_date, to_date=to_date)
        store_info = self.get_store_location()

        return {
            "status": "success",
            "attendance_list": attendance_list,
            "sales_kpi": kpi_data.get("kpi", []),
            "kpi_summary": {
                "period_label": kpi_data.get("period_label"),
                "total_sales": kpi_data.get("total_sales"),
                "total_sales_formatted": kpi_data.get("formatted_total_sales"),
                "total_bonus": kpi_data.get("total_bonus"),
                "total_bonus_formatted": kpi_data.get("formatted_total_bonus"),
                "total_payable_bonus": kpi_data.get("total_payable_bonus"),
                "total_payable_bonus_formatted": kpi_data.get("formatted_total_payable_bonus"),
                "frozen_bonus": kpi_data.get("frozen_bonus"),
                "frozen_bonus_formatted": kpi_data.get("formatted_frozen_bonus"),
                "bonus_percent": kpi_data.get("bonus_percent")
            },
            "store_location": store_info
        }

    async def get_employees(self) -> List[Dict[str, Any]]:
        """Get list of active employees from MoySklad or fallback."""
        try:
            resp = await self.ms_client._request("GET", "/entity/employee", params={"limit": 100})
            rows = resp.get("rows", [])
            active = []
            idx = 1001
            for r in rows:
                if not r.get("archived", False):
                    active.append({
                        "id": idx,
                        "moysklad_id": r.get("id"),
                        "name": r.get("name", "Ходим")
                    })
                    idx += 1
            if active:
                return active
        except Exception as e:
            logger.warning("moysklad_get_employees_failed", error=str(e))

        # Fallback employees
        return [
            {"id": 1001, "name": "Латипов Ф. Ф."},
            {"id": 1002, "name": "Lola"},
            {"id": 1003, "name": "Алишер Каримов"},
            {"id": 1004, "name": "Ferro Soft"}
        ]


hr_service = HRService()

