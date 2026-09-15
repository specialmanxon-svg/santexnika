import axios from 'axios';
import { FieldVisitCheckIn, FieldVisitResponse } from '../types';

const API_URL = import.meta.env.VITE_API_URL || '';

// Access Telegram WebApp initData directly
const getInitData = () => {
  if (typeof window !== 'undefined' && (window as any).Telegram?.WebApp) {
    return (window as any).Telegram.WebApp.initData;
  }
  return '';
};

const apiClient = axios.create({
  baseURL: API_URL,
});

apiClient.interceptors.request.use((config) => {
  const initData = getInitData();
  if (initData) {
    config.headers.Authorization = `tma ${initData}`;
  }
  return config;
});

export const api = {
  submitCheckIn: async (data: FieldVisitCheckIn, photoFile?: File): Promise<FieldVisitResponse> => {
    // If photoFile is provided, we should send as multipart/form-data
    if (photoFile) {
      const formData = new FormData();
      formData.append('payload', JSON.stringify(data));
      formData.append('photo', photoFile);

      const response = await apiClient.post<FieldVisitResponse>('/api/v1/visits/check-in-multipart', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
      return response.data;
    }

    const response = await apiClient.post<FieldVisitResponse>('/api/v1/visits/check-in', data);
    return response.data;
  },
};
