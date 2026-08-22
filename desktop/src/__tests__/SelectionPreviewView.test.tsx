import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import SelectionPreviewView from '../renderer/components/SelectionPreviewView';

const mockSetVoiceRecorderMuted = jest.fn();
const mockCloseVoiceRecorder = jest.fn();
const mockPlayerStop = jest.fn();

jest.mock('../renderer/selection-pcm-recorder', () => ({
  closeVoiceRecorder: () => mockCloseVoiceRecorder(),
  prewarmVoiceRecorder: jest.fn().mockResolvedValue(undefined),
  setVoiceRecorderMuted: (muted: boolean) => mockSetVoiceRecorderMuted(muted),
  startVoiceRecorder: jest.fn(),
}));

jest.mock('../renderer/pcm-stream-player', () => ({
  __esModule: true,
  default: jest.fn().mockImplementation(() => ({
    dispose: jest.fn(),
    enqueue: jest.fn(),
    prepare: jest.fn(),
    stop: () => mockPlayerStop(),
    whenIdle: jest.fn().mockResolvedValue(undefined),
  })),
}));

describe('SelectionPreviewView microphone mute', () => {
  let sendMessage: jest.Mock;

  beforeEach(() => {
    sendMessage = jest.fn();
    mockSetVoiceRecorderMuted.mockClear();
    mockCloseVoiceRecorder.mockClear();
    mockPlayerStop.mockClear();
    (window as any).electron = {
      ipcRenderer: {
        invoke: jest.fn(),
        on: jest.fn(() => jest.fn()),
        sendMessage,
      },
    };
  });

  it('mutes only the microphone without cancelling voice or playback', () => {
    render(<SelectionPreviewView />);

    fireEvent.click(screen.getByRole('button', { name: 'Mute microphone' }));

    expect(mockSetVoiceRecorderMuted).toHaveBeenCalledWith(true);
    expect(sendMessage).not.toHaveBeenCalledWith('selection-voice-cancel');
    expect(mockPlayerStop).not.toHaveBeenCalled();
    expect(
      screen.getByText('Microphone muted. Coco audio will keep playing.'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Unmute microphone' }),
    ).toHaveAttribute('aria-pressed', 'true');
  });
});
