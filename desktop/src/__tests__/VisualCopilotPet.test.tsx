import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import VisualCopilotPet from '../renderer/components/VisualCopilotPet';

describe('VisualCopilotPet', () => {
  it('starts an explicit selection when Coco is clicked', () => {
    const sendMessage = jest.fn();
    (window as any).electron = {
      ipcRenderer: {
        sendMessage,
      },
    };

    render(<VisualCopilotPet />);
    fireEvent.click(
      screen.getByRole('button', { name: 'Select an area with Coco' }),
    );

    expect(sendMessage).toHaveBeenCalledWith('selection-activate');
  });
});
