export interface GeoLocation {
  latitude: number;
  longitude: number;
  accuracy: number;
  is_mock: boolean;
}

export interface FieldVisitCheckIn {
  task_id: number;
  deal_id?: number;
  location: GeoLocation;
  photo_datetime?: string;
  notes?: string;
}

export interface FieldVisitResponse {
  success: boolean;
  message: string;
  comment_id?: number;
}

export interface TelegramUser {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  language_code?: string;
}
