import React, { useState, useEffect } from 'react';
import { GeoCapture } from './GeoCapture';
import { PhotoCapture } from './PhotoCapture';
import { useTelegram } from '../hooks/useTelegram';
import { api } from '../api/client';
import { GeoLocation } from '../types';

export const VisitForm: React.FC = () => {
  const { tg, showAlert } = useTelegram();
  const [taskId, setTaskId] = useState('');
  const [dealId, setDealId] = useState('');
  const [notes, setNotes] = useState('');
  const [location, setLocation] = useState<GeoLocation | null>(null);
  const [photo, setPhoto] = useState<{ file: File; datetime?: string } | null>(null);

  const isFormValid = taskId && location && photo;

  useEffect(() => {
    if (isFormValid) {
      tg.MainButton.setText('ОТПРАВИТЬ ОТЧЕТ');
      tg.MainButton.show();
      tg.MainButton.onClick(handleSubmit);
    } else {
      tg.MainButton.hide();
    }
    return () => {
      tg.MainButton.offClick(handleSubmit);
    };
  }, [isFormValid, taskId, dealId, notes, location, photo]);

  const handleSubmit = async () => {
    if (!isFormValid) return;
    
    tg.MainButton.showProgress();

    try {
      await api.submitCheckIn({
        task_id: parseInt(taskId, 10),
        deal_id: dealId ? parseInt(dealId, 10) : undefined,
        location: location,
        photo_datetime: photo.datetime,
        notes: notes || undefined
      }, photo.file);
      
      tg.MainButton.hideProgress();
      showAlert('Отчет успешно отправлен!');
      setTimeout(() => {
        tg.close();
      }, 1500);
    } catch (err: any) {
      tg.MainButton.hideProgress();
      const msg = err.response?.data?.message || err.message;
      showAlert(`Ошибка: ${msg}`);
    }
  };

  const inputStyle = {
    width: '100%',
    padding: '12px',
    marginBottom: '16px',
    borderRadius: '8px',
    border: '1px solid var(--tg-theme-hint-color, #ccc)',
    backgroundColor: 'var(--tg-theme-bg-color, #fff)',
    color: 'var(--tg-theme-text-color, #000)',
    boxSizing: 'border-box' as const,
    fontSize: '16px'
  };

  return (
    <div style={{ padding: '16px' }}>
      <input
        type="number"
        placeholder="Task ID (Обязательно)"
        value={taskId}
        onChange={(e) => setTaskId(e.target.value)}
        style={inputStyle}
      />
      
      <input
        type="number"
        placeholder="Deal ID (Опционально)"
        value={dealId}
        onChange={(e) => setDealId(e.target.value)}
        style={inputStyle}
      />

      <GeoCapture onCapture={setLocation} />
      
      <PhotoCapture onCapture={(file, datetime) => setPhoto({ file, datetime })} />

      <textarea
        placeholder="Заметки (Опционально)"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        style={{ ...inputStyle, minHeight: '80px', resize: 'vertical' }}
      />
    </div>
  );
};
