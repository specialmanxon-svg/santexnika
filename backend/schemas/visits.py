from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime

class GeoLocation(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    accuracy: float = Field(..., description="Accuracy in meters")
    is_mock: bool = Field(default=False, description="Whether location is from mock provider")
    
    @field_validator('accuracy')
    @classmethod
    def accuracy_must_be_reasonable(cls, v: float) -> float:
        if v > 20.0:
            raise ValueError(f'Location accuracy {v}m exceeds maximum allowed 20.0m')
        return v
    
    @field_validator('is_mock')
    @classmethod
    def reject_mock_location(cls, v: bool) -> bool:
        if v:
            raise ValueError('Mock/fake GPS locations are not allowed')
        return v

class FieldVisitCheckIn(BaseModel):
    task_id: int = Field(..., description="Bitrix24 task ID")
    deal_id: Optional[int] = Field(None, description="Related Bitrix24 deal ID")
    location: GeoLocation
    photo_datetime: Optional[datetime] = Field(None, description="EXIF DateTimeOriginal from photo")
    notes: Optional[str] = None

class FieldVisitResponse(BaseModel):
    success: bool
    message: str
    comment_id: Optional[int] = None
