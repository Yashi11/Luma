import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import SelectionOverlay from '../renderer/components/SelectionOverlay';

describe('SelectionOverlay', () => {
  let sendMessage: jest.Mock;

  beforeEach(() => {
    Object.defineProperty(window, 'PointerEvent', {
      configurable: true,
      value: MouseEvent,
    });
    sendMessage = jest.fn();
    (window as any).electron = {
      ipcRenderer: {
        sendMessage,
      },
    };
  });

  it('keeps rectangle selection available', () => {
    const { container } = render(<SelectionOverlay />);
    const overlay = container.querySelector(
      '.selection-overlay',
    ) as HTMLElement;

    fireEvent.pointerDown(overlay, { clientX: 20, clientY: 30, pointerId: 1 });
    fireEvent.pointerMove(overlay, { clientX: 80, clientY: 90, pointerId: 1 });
    fireEvent.pointerUp(overlay, { clientX: 80, clientY: 90, pointerId: 1 });

    expect(sendMessage).toHaveBeenCalledWith('selection-complete', {
      type: 'rectangle',
      x: 20,
      y: 30,
      width: 60,
      height: 60,
    });
  });

  it('sends a bounded freeform path instead of only a rectangle', () => {
    const { container } = render(<SelectionOverlay />);
    const overlay = container.querySelector(
      '.selection-overlay',
    ) as HTMLElement;
    fireEvent.click(screen.getByRole('button', { name: 'Freeform' }));

    fireEvent.pointerDown(overlay, { clientX: 20, clientY: 20, pointerId: 2 });
    fireEvent.pointerMove(overlay, { clientX: 90, clientY: 20, pointerId: 2 });
    fireEvent.pointerMove(overlay, { clientX: 90, clientY: 90, pointerId: 2 });
    fireEvent.pointerUp(overlay, { clientX: 20, clientY: 90, pointerId: 2 });

    expect(sendMessage).toHaveBeenCalledWith('selection-complete', {
      type: 'freeform',
      x: 20,
      y: 20,
      width: 70,
      height: 70,
      points: [
        { x: 20, y: 20 },
        { x: 90, y: 20 },
        { x: 90, y: 90 },
        { x: 20, y: 90 },
      ],
    });
  });

  it('cancels with Escape', () => {
    render(<SelectionOverlay />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(sendMessage).toHaveBeenCalledWith('selection-cancel');
  });
});
