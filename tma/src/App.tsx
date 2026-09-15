import React from 'react';
import { VisitForm } from './components/VisitForm';

function App() {
  return (
    <div>
      <header style={{
        padding: '16px',
        backgroundColor: 'var(--tg-theme-secondary-bg-color, #f0f0f0)',
        borderBottom: '1px solid var(--tg-theme-hint-color, #ccc)',
        textAlign: 'center',
        fontWeight: 'bold',
        fontSize: '18px'
      }}>
        Diyorgroup — Фиксация выезда
      </header>
      <VisitForm />
    </div>
  );
}

export default App;
