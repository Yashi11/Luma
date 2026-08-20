import { PointerEvent, useEffect, useMemo, useState } from 'react';
import './SelectionSurface.css';

type Point = { x: number; y: number };

export default function SelectionOverlay() {
  const [origin, setOrigin] = useState<Point | null>(null);
  const [cursor, setCursor] = useState<Point | null>(null);

  useEffect(() => {
    const cancel = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        window.electron.ipcRenderer.sendMessage('selection-cancel');
      }
    };
    window.addEventListener('keydown', cancel);
    return () => window.removeEventListener('keydown', cancel);
  }, []);

  const rectangle = useMemo(() => {
    if (!origin || !cursor) return null;
    return {
      x: Math.min(origin.x, cursor.x),
      y: Math.min(origin.y, cursor.y),
      width: Math.abs(cursor.x - origin.x),
      height: Math.abs(cursor.y - origin.y),
    };
  }, [origin, cursor]);

  const begin = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = { x: event.clientX, y: event.clientY };
    setOrigin(point);
    setCursor(point);
  };

  const move = (event: PointerEvent<HTMLDivElement>) => {
    if (origin) setCursor({ x: event.clientX, y: event.clientY });
  };

  const finish = (event: PointerEvent<HTMLDivElement>) => {
    if (!origin) return;
    const finalRectangle = {
      x: Math.min(origin.x, event.clientX),
      y: Math.min(origin.y, event.clientY),
      width: Math.abs(event.clientX - origin.x),
      height: Math.abs(event.clientY - origin.y),
    };
    if (finalRectangle.width >= 24 && finalRectangle.height >= 24) {
      window.electron.ipcRenderer.sendMessage('selection-complete', {
        type: 'rectangle',
        ...finalRectangle,
      });
    } else {
      setOrigin(null);
      setCursor(null);
    }
  };

  return (
    <div
      className="selection-overlay"
      role="presentation"
      onPointerDown={begin}
      onPointerMove={move}
      onPointerUp={finish}
    >
      <div className="selection-instruction">
        <span className="selection-mark" aria-hidden>
          ⌖
        </span>
        <div>
          <strong>Select what you want explained</strong>
          <span>Drag a rectangle · Esc to cancel</span>
        </div>
      </div>
      {rectangle && (
        <div
          className="selection-frame"
          style={{
            left: rectangle.x,
            top: rectangle.y,
            width: rectangle.width,
            height: rectangle.height,
          }}
        >
          <span className="selection-size">
            {Math.round(rectangle.width)} × {Math.round(rectangle.height)}
          </span>
          <i className="selection-corner selection-corner--tl" />
          <i className="selection-corner selection-corner--tr" />
          <i className="selection-corner selection-corner--bl" />
          <i className="selection-corner selection-corner--br" />
        </div>
      )}
    </div>
  );
}
