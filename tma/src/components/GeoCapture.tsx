import React, { useState } from 'react';
import { GeoLocation } from '../types';

interface GeoCaptureProps {
  onCapture: (location: GeoLocation) => void;
}

export const GeoCapture: React.FC<GeoCaptureProps> = ({ onCapture }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [location, setLocation] = useState<GeoLocation | null>(null);

  const handleCapture = () => {
    setLoading(true);
    setError(null);

    if (!navigator.geolocation) {
      setError('Геолокация не поддерживается вашим браузером');
      setLoading(false);
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLoading(false);
        const coords = position.coords as any;
        
        // Mock detection: some platforms add isMock or mock property
        const is_mock = coords.isMock === true || coords.mock === true;

        if (is_mock) {
          setError('Обнаружена фиктивная геолокация. Пожалуйста, отключите Fake GPS.');
          return;
        }

        if (coords.accuracy > 200) { // Adjust threshold if 20m is too strict (often 20m is very hard indoors)
          // For strict 20m requirement as per spec:
          // if (coords.accuracy > 20)
          setError(`Точность геолокации слишком низкая (${Math.round(coords.accuracy)}м). Требуется < 200м.`);
          return;
        }

        const geo: GeoLocation = {
          latitude: coords.latitude,
          longitude: coords.longitude,
          accuracy: coords.accuracy,
          is_mock,
        };

        setLocation(geo);
        onCapture(geo);
      },
      (err) => {
        setLoading(false);
        setError(`Ошибка получения локации: ${err.message}`);
      },
      {
        enableHighAccuracy: true,
        timeout: 10000,
        maximumAge: 0,
      }
    );
  };

  return (
    <div style={{ marginBottom: '16px' }}>
      <button 
        type="button" 
        onClick={handleCapture}
        disabled={loading}
        style={{
          width: '100%',
          padding: '12px',
          backgroundColor: 'var(--tg-theme-button-color, #3390ec)',
          color: 'var(--tg-theme-button-text-color, #ffffff)',
          border: 'none',
          borderRadius: '8px',
          fontSize: '16px'
        }}
      >
        {loading ? 'Получение локации...' : 'Зафиксировать координаты'}
      </button>

      {error && (
        <p style={{ color: 'var(--tg-theme-destructive-text-color, #ff3b30)', marginTop: '8px' }}>
          {error}
        </p>
      )}

      {location && (
        <div style={{ marginTop: '8px', fontSize: '14px', color: 'var(--tg-theme-hint-color, #999999)' }}>
          ✅ Локация зафиксирована (Точность: {Math.round(location.accuracy)}м)
          <br/>
          <a 
            href={`https://www.google.com/maps/search/?api=1&query=${location.latitude},${location.longitude}`}
            target="_blank"
            rel="noreferrer"
            style={{ color: 'var(--tg-theme-link-color, #3390ec)' }}
          >
            Посмотреть на карте
          </a>
        </div>
      )}
    </div>
  );
};
