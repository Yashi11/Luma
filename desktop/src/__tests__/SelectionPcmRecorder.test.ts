import {
  closeVoiceRecorder,
  setVoiceRecorderMuted,
  startVoiceRecorder,
} from '../renderer/selection-pcm-recorder';

describe('selection PCM recorder mute', () => {
  const track = { enabled: true, stop: jest.fn() };
  const source = { connect: jest.fn(), disconnect: jest.fn() };
  const processor = {
    connect: jest.fn(),
    disconnect: jest.fn(),
    onaudioprocess: null as ((event: AudioProcessingEvent) => void) | null,
  };
  const mutedOutput = {
    gain: { value: 1 },
    connect: jest.fn(),
    disconnect: jest.fn(),
  };
  const stream = {
    getAudioTracks: jest.fn(() => [track]),
    getTracks: jest.fn(() => [track]),
  };
  const context = {
    state: 'running',
    sampleRate: 16_000,
    destination: {},
    resume: jest.fn().mockResolvedValue(undefined),
    close: jest.fn().mockResolvedValue(undefined),
    createMediaStreamSource: jest.fn(() => source),
    createScriptProcessor: jest.fn(() => processor),
    createGain: jest.fn(() => mutedOutput),
  };

  beforeEach(() => {
    jest.useFakeTimers();
    track.enabled = true;
    track.stop.mockClear();
    source.connect.mockClear();
    source.disconnect.mockClear();
    processor.connect.mockClear();
    processor.disconnect.mockClear();
    processor.onaudioprocess = null;
    mutedOutput.connect.mockClear();
    mutedOutput.disconnect.mockClear();
    context.close.mockClear();
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: jest.fn().mockResolvedValue(stream) },
    });
    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      value: jest.fn(() => context),
    });
  });

  afterEach(() => {
    closeVoiceRecorder();
    jest.useRealTimers();
  });

  const audioEvent = {
    inputBuffer: {
      getChannelData: () => new Float32Array(1_600).fill(0.5),
    },
  } as unknown as AudioProcessingEvent;

  it('finalizes detected speech after 1.5 seconds of muted silence', async () => {
    const onPcmChunk = jest.fn();
    const recorder = await startVoiceRecorder({
      maxDurationMs: 30_000,
      minimumVoiceMs: 0,
      speechThreshold: 0,
      onPcmChunk,
    });
    const completion = recorder.done.then(
      () => 'completed',
      () => 'cancelled',
    );

    processor.onaudioprocess?.(audioEvent);
    expect(onPcmChunk).toHaveBeenCalledTimes(1);

    setVoiceRecorderMuted(true);
    expect(track.enabled).toBe(false);
    processor.onaudioprocess?.(audioEvent);
    expect(onPcmChunk).toHaveBeenCalledTimes(1);

    jest.advanceTimersByTime(1_499);
    expect(await Promise.race([completion, Promise.resolve('pending')])).toBe(
      'pending',
    );
    jest.advanceTimersByTime(1);
    await expect(completion).resolves.toBe('completed');
  });

  it('keeps an empty muted turn paused until the microphone is unmuted', async () => {
    const onPcmChunk = jest.fn();
    const recorder = await startVoiceRecorder({
      maxDurationMs: 30_000,
      minimumVoiceMs: 0,
      speechThreshold: 0,
      onPcmChunk,
    });
    const completion = recorder.done.then(
      () => 'completed',
      () => 'cancelled',
    );

    setVoiceRecorderMuted(true);
    jest.advanceTimersByTime(60_000);
    expect(await Promise.race([completion, Promise.resolve('pending')])).toBe(
      'pending',
    );

    setVoiceRecorderMuted(false);
    expect(track.enabled).toBe(true);
    processor.onaudioprocess?.(audioEvent);
    expect(onPcmChunk).toHaveBeenCalledTimes(1);

    recorder.stop();
    await expect(completion).resolves.toBe('completed');
  });
});
