export type VoiceRecorderStatus = 'listening' | 'speaking';

export interface VoiceRecording {
  durationMs: number;
}

export interface ActiveVoiceRecorder {
  done: Promise<VoiceRecording>;
  stop: () => void;
  cancel: () => void;
}

interface VoiceRecorderOptions {
  silenceMs?: number;
  maxDurationMs?: number;
  speechThreshold?: number;
  minimumVoiceMs?: number;
  onSpeechStart?: () => void;
  onPcmChunk: (pcmBase64: string) => void;
  onStatus?: (status: VoiceRecorderStatus) => void;
}

interface ActiveTurn {
  options: VoiceRecorderOptions;
  startedAt: number;
  speechDetected: boolean;
  speechCandidateMs: number;
  lastVoiceAt: number;
  noiseRms: number;
  noiseFrames: number;
  preSpeechChunks: string[];
  muted: boolean;
  timeoutRemainingMs: number;
  timeoutStartedAt: number;
  settled: boolean;
  muteFinalizeTimer?: number;
  timer?: number;
  resolve: (recording: VoiceRecording) => void;
  reject: (error: Error) => void;
}

interface RecorderCapture {
  stream: MediaStream;
  context: AudioContext;
  source: MediaStreamAudioSourceNode;
  processor: ScriptProcessorNode;
  mutedOutput: GainNode;
  activeTurn?: ActiveTurn;
}

const TARGET_SAMPLE_RATE = 16_000;
const MIN_RMS = 0.012;
const SPEECH_CONFIRM_MS = 120;
const DEFAULT_END_OF_SPEECH_SILENCE_MS = 1_500;
const PRE_SPEECH_PCM_CHUNKS = 6;

let capture: RecorderCapture | undefined;
let capturePromise: Promise<RecorderCapture> | undefined;
let captureMuted = false;
let generation = 0;

function resample(input: Float32Array, inputRate: number): Float32Array {
  if (inputRate === TARGET_SAMPLE_RATE) return input;
  const outputLength = Math.max(
    1,
    Math.round(input.length * (TARGET_SAMPLE_RATE / inputRate)),
  );
  const output = new Float32Array(outputLength);
  const ratio = inputRate / TARGET_SAMPLE_RATE;
  for (let i = 0; i < outputLength; i += 1) {
    const position = i * ratio;
    const left = Math.floor(position);
    const right = Math.min(left + 1, input.length - 1);
    const fraction = position - left;
    output[i] = input[left] * (1 - fraction) + input[right] * fraction;
  }
  return output;
}

function encodePcm16(samples: Float32Array): Uint8Array {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < samples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return bytes;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

function settleTurn(current: RecorderCapture, turn: ActiveTurn, error?: Error) {
  if (turn.settled) return;
  turn.settled = true;
  if (turn.timer !== undefined) window.clearTimeout(turn.timer);
  if (turn.muteFinalizeTimer !== undefined) {
    window.clearTimeout(turn.muteFinalizeTimer);
  }
  turn.timer = undefined;
  turn.muteFinalizeTimer = undefined;
  if (current.activeTurn === turn) current.activeTurn = undefined;
  if (error) turn.reject(error);
  else turn.resolve({ durationMs: performance.now() - turn.startedAt });
}

function finishTurn(
  current: RecorderCapture,
  turn: ActiveTurn,
  requireSpeech: boolean,
) {
  if (requireSpeech && !turn.speechDetected) {
    settleTurn(
      current,
      turn,
      new Error(
        "I didn't hear any speech. Try again a little closer to the microphone.",
      ),
    );
    return;
  }
  settleTurn(current, turn);
}

function scheduleTurnTimeout(current: RecorderCapture, turn: ActiveTurn) {
  if (turn.settled || turn.muted) return;
  turn.timeoutStartedAt = performance.now();
  turn.timer = window.setTimeout(
    () => finishTurn(current, turn, true),
    Math.max(0, turn.timeoutRemainingMs),
  );
}

function applyCaptureMuted(current: RecorderCapture, muted: boolean) {
  current.stream.getAudioTracks().forEach((track) => {
    track.enabled = !muted;
  });
  const turn = current.activeTurn;
  if (!turn || turn.settled || turn.muted === muted) return;
  turn.muted = muted;
  if (muted) {
    if (turn.timer !== undefined) {
      window.clearTimeout(turn.timer);
      turn.timer = undefined;
      turn.timeoutRemainingMs = Math.max(
        0,
        turn.timeoutRemainingMs - (performance.now() - turn.timeoutStartedAt),
      );
    }
    turn.speechCandidateMs = 0;
    turn.preSpeechChunks.length = 0;
    if (turn.speechDetected) {
      const silenceMs =
        turn.options.silenceMs ?? DEFAULT_END_OF_SPEECH_SILENCE_MS;
      turn.muteFinalizeTimer = window.setTimeout(
        () => finishTurn(current, turn, false),
        Math.max(0, silenceMs - (performance.now() - turn.lastVoiceAt)),
      );
    }
    return;
  }
  if (turn.muteFinalizeTimer !== undefined) {
    window.clearTimeout(turn.muteFinalizeTimer);
    turn.muteFinalizeTimer = undefined;
  }
  turn.lastVoiceAt = performance.now();
  turn.options.onStatus?.(turn.speechDetected ? 'speaking' : 'listening');
  scheduleTurnTimeout(current, turn);
}

async function ensureCapture(): Promise<RecorderCapture> {
  if (capture) {
    if (capture.context.state === 'suspended') await capture.context.resume();
    return capture;
  }
  if (capturePromise) return capturePromise;
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('Microphone recording is not available on this computer.');
  }
  const requestedGeneration = generation;
  const pending = (async () => {
    const mediaPromise = navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    let mediaTimeout: number | undefined;
    const timeoutPromise = new Promise<never>((_resolve, reject) => {
      mediaTimeout = window.setTimeout(
        () =>
          reject(
            new Error(
              'The microphone did not become ready. Close any app holding it and try again.',
            ),
          ),
        10_000,
      );
    });
    let stream: MediaStream;
    try {
      stream = await Promise.race([mediaPromise, timeoutPromise]);
    } catch (error) {
      mediaPromise
        .then((lateStream) =>
          lateStream.getTracks().forEach((track) => track.stop()),
        )
        .catch(() => undefined);
      throw error;
    } finally {
      if (mediaTimeout !== undefined) window.clearTimeout(mediaTimeout);
    }
    if (requestedGeneration !== generation) {
      stream.getTracks().forEach((track) => track.stop());
      throw new Error('Voice recording cancelled.');
    }
    stream.getAudioTracks().forEach((track) => {
      track.enabled = !captureMuted;
    });
    const context = new AudioContext();
    if (context.state === 'suspended') await context.resume();
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(4096, 1, 1);
    const mutedOutput = context.createGain();
    mutedOutput.gain.value = 0;
    source.connect(processor);
    processor.connect(mutedOutput);
    mutedOutput.connect(context.destination);
    const current: RecorderCapture = {
      stream,
      context,
      source,
      processor,
      mutedOutput,
    };
    processor.onaudioprocess = (event) => {
      if (capture !== current) return;
      const turn = current.activeTurn;
      if (!turn || turn.settled || turn.muted) return;
      const input = new Float32Array(event.inputBuffer.getChannelData(0));
      const pcm = bytesToBase64(
        encodePcm16(resample(input, current.context.sampleRate)),
      );

      let energy = 0;
      for (let i = 0; i < input.length; i += 1) energy += input[i] * input[i];
      const rms = Math.sqrt(energy / Math.max(1, input.length));
      const chunkMs = (input.length / current.context.sampleRate) * 1_000;
      if (!turn.speechDetected && rms < 0.03) {
        turn.noiseRms =
          (turn.noiseRms * turn.noiseFrames + rms) / (turn.noiseFrames + 1);
        turn.noiseFrames += 1;
      }
      const threshold = Math.max(
        turn.options.speechThreshold ?? MIN_RMS,
        turn.noiseRms * 2.5,
      );
      const now = performance.now();
      if (rms >= threshold) {
        turn.speechCandidateMs += chunkMs;
        turn.lastVoiceAt = now;
        if (
          !turn.speechDetected &&
          turn.speechCandidateMs >=
            (turn.options.minimumVoiceMs ?? SPEECH_CONFIRM_MS)
        ) {
          turn.speechDetected = true;
          turn.options.onSpeechStart?.();
          turn.preSpeechChunks.forEach(turn.options.onPcmChunk);
          turn.preSpeechChunks.length = 0;
          turn.options.onStatus?.('speaking');
        }
      } else if (!turn.speechDetected) {
        turn.speechCandidateMs = Math.max(
          0,
          turn.speechCandidateMs - chunkMs / 2,
        );
      }

      if (turn.speechDetected) turn.options.onPcmChunk(pcm);
      else {
        turn.preSpeechChunks.push(pcm);
        if (turn.preSpeechChunks.length > PRE_SPEECH_PCM_CHUNKS) {
          turn.preSpeechChunks.shift();
        }
      }
      const silenceMs =
        turn.options.silenceMs ?? DEFAULT_END_OF_SPEECH_SILENCE_MS;
      if (turn.speechDetected && now - turn.lastVoiceAt >= silenceMs) {
        finishTurn(current, turn, false);
      }
    };
    capture = current;
    return current;
  })();
  capturePromise = pending;
  pending
    .finally(() => {
      if (capturePromise === pending) capturePromise = undefined;
    })
    .catch(() => undefined);
  return pending;
}

export async function prewarmVoiceRecorder(): Promise<void> {
  await ensureCapture();
}

export function setVoiceRecorderMuted(muted: boolean): void {
  captureMuted = muted;
  if (capture) applyCaptureMuted(capture, muted);
}

export function closeVoiceRecorder(): void {
  generation += 1;
  captureMuted = false;
  const current = capture;
  capture = undefined;
  capturePromise = undefined;
  if (!current) return;
  if (current.activeTurn) {
    settleTurn(
      current,
      current.activeTurn,
      new Error('Voice recording cancelled.'),
    );
  }
  current.processor.onaudioprocess = null;
  current.source.disconnect();
  current.processor.disconnect();
  current.mutedOutput.disconnect();
  current.stream.getTracks().forEach((track) => track.stop());
  current.context.close().catch(() => undefined);
}

export async function startVoiceRecorder(
  options: VoiceRecorderOptions,
): Promise<ActiveVoiceRecorder> {
  const current = await ensureCapture();
  if (captureMuted) throw new Error('Voice recording cancelled.');
  if (current.activeTurn) {
    throw new Error('A microphone turn is already active.');
  }
  let resolveDone: (recording: VoiceRecording) => void = () => {};
  let rejectDone: (error: Error) => void = () => {};
  const done = new Promise<VoiceRecording>((resolve, reject) => {
    resolveDone = resolve;
    rejectDone = reject;
  });
  const turn: ActiveTurn = {
    options,
    startedAt: performance.now(),
    speechDetected: false,
    speechCandidateMs: 0,
    lastVoiceAt: performance.now(),
    noiseRms: 0.004,
    noiseFrames: 0,
    preSpeechChunks: [],
    muted: false,
    timeoutRemainingMs: options.maxDurationMs ?? 30_000,
    timeoutStartedAt: performance.now(),
    settled: false,
    resolve: resolveDone,
    reject: rejectDone,
  };
  current.activeTurn = turn;
  options.onStatus?.('listening');
  scheduleTurnTimeout(current, turn);
  return {
    done,
    stop: () => finishTurn(current, turn, false),
    cancel: () =>
      settleTurn(current, turn, new Error('Voice recording cancelled.')),
  };
}
