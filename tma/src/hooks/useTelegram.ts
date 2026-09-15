import { useEffect, useState } from 'react';
import WebApp from '@twa-dev/sdk';

export function useTelegram() {
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    WebApp.ready();
    WebApp.expand();
    setIsReady(true);
  }, []);

  return {
    tg: WebApp,
    user: WebApp.initDataUnsafe?.user,
    initData: WebApp.initData,
    queryId: WebApp.initDataUnsafe?.query_id,
    themeParams: WebApp.themeParams,
    colorScheme: WebApp.colorScheme,
    isReady,
    showMainButton: (text: string, onClick: () => void) => {
      WebApp.MainButton.setText(text);
      WebApp.MainButton.onClick(onClick);
      WebApp.MainButton.show();
    },
    hideMainButton: () => {
      WebApp.MainButton.hide();
    },
    showBackButton: (onClick: () => void) => {
      WebApp.BackButton.onClick(onClick);
      WebApp.BackButton.show();
    },
    hideBackButton: () => {
      WebApp.BackButton.hide();
    },
    showAlert: (message: string) => WebApp.showAlert(message),
    showConfirm: (message: string) => new Promise<boolean>((resolve) => WebApp.showConfirm(message, resolve)),
    closeApp: () => WebApp.close(),
    sendData: (data: any) => WebApp.sendData(JSON.stringify(data)),
  };
}
