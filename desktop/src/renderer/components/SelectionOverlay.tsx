import { PointerEvent, useEffect, useMemo, useState } from 'react';
import './SelectionSurface.css';

type Point = { x: number; y: number };
type SelectionMode = 'rectangle' | 'freeform';

const MINIMUM_SIZE = 24;
const MINIMUM_FREEFORM_AREA = (MINIMUM_SIZE * MINIMUM_SIZE) / 4;
const MAX_FREEFORM_POINTS = 512;
const POINT_SPACING = 3;

function boundsOf(points: Point[]) {
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  return {
    x,
    y,
    width: Math.max(...xs) - x,
    height: Math.max(...ys) - y,
  };
}

function polygonArea(points: Point[]) {
  return Math.abs(
    points.reduce((area, point, index) => {
      const next = points[(index + 1) % points.length];
      return area + point.x * next.y - next.x * point.y;
    }, 0) / 2,
  );
}

function appendPoint(points: Point[], point: Point, spacing = POINT_SPACING) {
  const previous = points[points.length - 1];
  if (
    points.length >= MAX_FREEFORM_POINTS ||
    (previous &&
      Math.hypot(point.x - previous.x, point.y - previous.y) < spacing)
  ) {
    return points;
  }
  return [...points, point];
}

function pathFrom(points: Point[]) {
  if (!points.length) return '';
  return `M ${points.map((point) => `${point.x} ${point.y}`).join(' L ')}`;
}

export default function SelectionOverlay() {
  const [mode, setMode] = useState<SelectionMode>('rectangle');
  const [origin, setOrigin] = useState<Point | null>(null);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [freeformPoints, setFreeformPoints] = useState<Point[]>([]);

  useEffect(() => {
    const cancel = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        window.electron?.ipcRenderer.sendMessage('selection-cancel');
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

  const freeformBounds = useMemo(
    () => (freeformPoints.length ? boundsOf(freeformPoints) : null),
    [freeformPoints],
  );
  const freeformPath = useMemo(
    () => pathFrom(freeformPoints),
    [freeformPoints],
  );

  const resetDrawing = () => {
    setOrigin(null);
    setCursor(null);
    setFreeformPoints([]);
  };

  const chooseMode = (nextMode: SelectionMode) => {
    resetDrawing();
    setMode(nextMode);
  };

  const begin = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const point = { x: event.clientX, y: event.clientY };
    if (mode === 'rectangle') {
      setOrigin(point);
      setCursor(point);
    } else {
      setFreeformPoints([point]);
    }
  };

  const move = (event: PointerEvent<HTMLDivElement>) => {
    const point = { x: event.clientX, y: event.clientY };
    if (mode === 'rectangle' && origin) {
      setCursor(point);
    } else if (mode === 'freeform' && freeformPoints.length) {
      setFreeformPoints((points) => appendPoint(points, point));
    }
  };

  const finish = (event: PointerEvent<HTMLDivElement>) => {
    if (mode === 'rectangle') {
      if (!origin) return;
      const finalRectangle = {
        x: Math.min(origin.x, event.clientX),
        y: Math.min(origin.y, event.clientY),
        width: Math.abs(event.clientX - origin.x),
        height: Math.abs(event.clientY - origin.y),
      };
      if (
        finalRectangle.width >= MINIMUM_SIZE &&
        finalRectangle.height >= MINIMUM_SIZE
      ) {
        window.electron?.ipcRenderer.sendMessage('selection-complete', {
          type: 'rectangle',
          ...finalRectangle,
        });
      } else {
        resetDrawing();
      }
      return;
    }

    if (!freeformPoints.length) return;
    const points = appendPoint(
      freeformPoints,
      { x: event.clientX, y: event.clientY },
      0,
    );
    const bounds = boundsOf(points);
    if (
      points.length >= 3 &&
      bounds.width >= MINIMUM_SIZE &&
      bounds.height >= MINIMUM_SIZE &&
      polygonArea(points) >= MINIMUM_FREEFORM_AREA
    ) {
      window.electron?.ipcRenderer.sendMessage('selection-complete', {
        type: 'freeform',
        ...bounds,
        points,
      });
    } else {
      resetDrawing();
    }
  };

  return (
    <div
      className="selection-overlay"
      role="presentation"
      onPointerDown={begin}
      onPointerMove={move}
      onPointerUp={finish}
      onPointerCancel={resetDrawing}
    >
      <div
        className="selection-instruction"
        onPointerDown={(event) => event.stopPropagation()}
      >
        <span className="selection-mark" aria-hidden>
          ⌖
        </span>
        <div className="selection-copy">
          <strong>Select what you want explained</strong>
          <span>
            {mode === 'rectangle'
              ? 'Drag to frame a region · Esc to cancel'
              : 'Draw around anything · Esc to cancel'}
          </span>
        </div>
        <div
          className="selection-shape-picker"
          role="group"
          aria-label="Selection shape"
        >
          <button
            type="button"
            className={mode === 'rectangle' ? 'is-active' : ''}
            aria-pressed={mode === 'rectangle'}
            onClick={() => chooseMode('rectangle')}
          >
            <span
              className="selection-shape-icon selection-shape-icon--rectangle"
              aria-hidden
            />
            Rectangle
          </button>
          <button
            type="button"
            className={mode === 'freeform' ? 'is-active' : ''}
            aria-pressed={mode === 'freeform'}
            onClick={() => chooseMode('freeform')}
          >
            <span
              className="selection-shape-icon selection-shape-icon--freeform"
              aria-hidden
            >
              ∿
            </span>
            Freeform
          </button>
        </div>
      </div>
      {mode === 'rectangle' && rectangle && (
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
      {mode === 'freeform' && freeformPoints.length > 1 && (
        <>
          <svg className="selection-lasso" aria-hidden>
            <path
              className="selection-lasso__shade"
              fillRule="evenodd"
              d={`M 0 0 H ${window.innerWidth} V ${window.innerHeight} H 0 Z ${freeformPath} Z`}
            />
            <path className="selection-lasso__fill" d={`${freeformPath} Z`} />
            <path className="selection-lasso__line" d={freeformPath} />
          </svg>
          {freeformBounds && (
            <span
              className="selection-lasso-size"
              style={{
                left: freeformBounds.x + freeformBounds.width,
                top: freeformBounds.y + freeformBounds.height,
              }}
            >
              {Math.round(freeformBounds.width)} ×{' '}
              {Math.round(freeformBounds.height)}
            </span>
          )}
        </>
      )}
    </div>
  );
}
