import { useEffect, useState } from 'react';

import PetSprite from './PetSprite';
import './VisualCopilotPet.css';

export default function VisualCopilotPet() {
  const [chatActive, setChatActive] = useState(false);

  useEffect(() => {
    const on = window.electron?.ipcRenderer?.on;
    if (typeof on !== 'function') return undefined;
    const cleanup = on('chat-stream-event', (data: any) => {
      if (!data?.requestId) return;
      setChatActive(
        data.type !== 'done' &&
          data.type !== 'error' &&
          data.type !== 'activity_done',
      );
    });
    return () => {
      if (typeof cleanup === 'function') cleanup();
    };
  }, []);

  const startSelection = () => {
    window.electron?.ipcRenderer.sendMessage('selection-activate');
  };

  return (
    <main className="visual-copilot-pet" aria-label="Luma Visual Copilot">
      <div className="visual-copilot-pet__hint" aria-hidden>
        Select with Luma
      </div>
      <div className="visual-copilot-pet__sprite">
        <PetSprite mood="idle" variant="luma" active={chatActive} />
        <span className="visual-copilot-pet__badge" aria-hidden>
          <span />
        </span>
        <button
          type="button"
          className="visual-copilot-pet__action"
          onClick={startSelection}
          title="Select an area with Luma"
          aria-label="Select an area with Luma"
        />
      </div>
    </main>
  );
}
