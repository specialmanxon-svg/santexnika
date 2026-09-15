"""Field visit validation service."""
import math
import structlog
from datetime import datetime, timezone
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image
from PIL.ExifTags import TAGS

from schemas.visits import GeoLocation, FieldVisitCheckIn, FieldVisitResponse
from services.bitrix_client import BitrixClient

logger = structlog.get_logger()

class VisitValidator:
    def __init__(self):
        self.bitrix = BitrixClient()

    async def validate_location(self, location: GeoLocation) -> bool:
        """Validate location accuracy is <= 20m and not mocked."""
        if location.is_mock:
            logger.warning("mock_location_detected", lat=location.lat, lng=location.lng)
            return False
            
        if location.accuracy > 20.0:
            logger.warning("poor_location_accuracy", accuracy=location.accuracy)
            return False
            
        return True

    async def validate_photo_timestamp(
        self, 
        exif_datetime: datetime, 
        server_time: datetime, 
        max_delta_seconds: int = 180
    ) -> bool:
        """Validate photo timestamp is within max delta of server time."""
        delta = abs((server_time - exif_datetime).total_seconds())
        if delta > max_delta_seconds:
            logger.warning("photo_timestamp_invalid", delta_seconds=delta)
            return False
        return True

    def _extract_exif_datetime(self, image_file) -> datetime | None:
        """Helper to extract datetime from image EXIF."""
        try:
            image = Image.open(image_file.file)
            exif_data = image._getexif()
            if not exif_data:
                return None
                
            for tag_id, value in exif_data.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag == 'DateTimeOriginal':
                    # Format is usually YYYY:MM:DD HH:MM:SS
                    return datetime.strptime(value, "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return None
        except Exception as e:
            logger.error("exif_extraction_failed", error=str(e))
            return None

    async def process_checkin(
        self, 
        checkin: FieldVisitCheckIn, 
        photo_file: UploadFile, 
        session: AsyncSession
    ) -> FieldVisitResponse:
        """Full validation pipeline for field visit checkin."""
        # 1. Validate location
        if not await self.validate_location(checkin.location):
            return FieldVisitResponse(success=False, reason="Invalid or mock location")

        # 2. Extract and validate EXIF
        exif_dt = self._extract_exif_datetime(photo_file)
        if not exif_dt:
            return FieldVisitResponse(success=False, reason="Missing EXIF timestamp")
            
        server_time = datetime.now(timezone.utc)
        if not await self.validate_photo_timestamp(exif_dt, server_time):
            return FieldVisitResponse(success=False, reason="Photo timestamp mismatch")
            
        # 3. Create task comment in Bitrix24
        map_link = f"https://maps.google.com/?q={checkin.location.lat},{checkin.location.lng}"
        comment_text = f"Field visit check-in verified.\nCoordinates: {checkin.location.lat}, {checkin.location.lng}\nMap: {map_link}"
        
        # In a real app we'd upload the file and link it, keeping this simple for the mock
        await self.bitrix.add_task_comment(checkin.task_id, comment_text)
        
        logger.info("field_visit_verified", task_id=checkin.task_id)
        return FieldVisitResponse(success=True, reason="Verified successfully")

visit_validator = VisitValidator()
