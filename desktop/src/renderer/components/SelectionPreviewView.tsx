import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import PcmStreamPlayer from '../pcm-stream-player';
import {
  closeVoiceRecorder,
  prewarmVoiceRecorder,
  setVoiceRecorderMuted,
  startVoiceRecorder,
} from '../selection-pcm-recorder';
import type { ActiveVoiceRecorder } from '../selection-pcm-recorder';
import './SelectionSurface.css';

type PreviewState = {
  imageDataUrl?: string;
  status: 'preview' | 'sending' | 'answer' | 'error';
  answer?: string;
  error?: string;
  turns?: Array<{ role: 'user' | 'assistant'; text: string }>;
};

type VoiceState =
  | 'idle'
  | 'requesting'
  | 'listening'
  | 'speaking'
  | 'transcribing'
  | 'answering';

type VoiceEvent = {
  type: string;
  action?: 'mute' | 'reselect' | 'close';
  text?: string;
  delta?: string;
  audio?: string;
  answer?: string;
  message?: string;
  sample_rate?: number;
};

const DEFAULT_CONTEXT_NUDGE =
  'I’ve got the selected area. What would you like me to look for?';

export default function SelectionPreviewView() {
  const [state, setState] = useState<PreviewState>({ status: 'preview' });
  const [question, setQuestion] = useState('');
  const [streamAnswer, setStreamAnswer] = useState('');
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [voiceError, setVoiceError] = useState('');
  const [contextNudge, setContextNudge] = useState(DEFAULT_CONTEXT_NUDGE);
  const [muted, setMuted] = useState(false);
  const recorderRef = useRef<ActiveVoiceRecorder | null>(null);
  const pcmPlayerRef = useRef(new PcmStreamPlayer());
  const audioReceivedRef = useRef(false);
  const nudgeActiveRef = useRef(true);
  const contextNudgeRef = useRef(DEFAULT_CONTEXT_NUDGE);
  const beginVoiceRef = useRef<() => Promise<void>>(async () => {});
  const setMutedRef = useRef<(nextMuted: boolean) => Promise<void>>(
    async () => {},
  );
  const mutedRef = useRef(false);
  const playbackGenerationRef = useRef(0);
  const wakeWordPausedRef = useRef(false);
  const voiceTurnStartingRef = useRef(false);
  const serverSpeechEndedRef = useRef(false);
  const mountedRef = useRef(true);
  const inputPrewarmedRef = useRef(false);

  const speakAnswer = useCallback((answer: string, onEnd?: () => void) => {
    if (!window.speechSynthesis || !answer.trim()) {
      onEnd?.();
      return;
    }
    window.speechSynthesis.cancel();
    const spokenText = answer
      .replace(/[`*_#>()[\]]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    const utterance = new SpeechSynthesisUtterance(spokenText);
    utterance.rate = 1;
    utterance.pitch = 1;
    if (onEnd) {
      utterance.onend = onEnd;
      utterance.onerror = onEnd;
    }
    window.speechSynthesis.speak(utterance);
  }, []);

  const stopSpeech = useCallback(() => {
    pcmPlayerRef.current.stop();
    window.speechSynthesis?.cancel();
  }, []);

  useEffect(() => {
    const pcmPlayer = pcmPlayerRef.current;
    const disposeState = window.electron.ipcRenderer.on(
      'selection-preview-state',
      (next) => setState(next as PreviewState),
    );
    const disposeVoice = window.electron.ipcRenderer.on(
      'selection-voice-event',
      (payload) => {
        const event = payload as VoiceEvent;
        if (event.type === 'nudge_start' && typeof event.text === 'string') {
          contextNudgeRef.current = event.text;
          setContextNudge(event.text);
        } else if (
          event.type === 'nudge_audio_chunk' &&
          typeof event.audio === 'string' &&
          nudgeActiveRef.current
        ) {
          pcmPlayerRef.current.enqueue(
            event.audio,
            typeof event.sample_rate === 'number' ? event.sample_rate : 24_000,
          );
        } else if (event.type === 'nudge_error' && nudgeActiveRef.current) {
          speakAnswer(contextNudgeRef.current, () => {
            if (!nudgeActiveRef.current || mutedRef.current) return;
            nudgeActiveRef.current = false;
            beginVoiceRef.current().catch(() => undefined);
          });
        } else if (event.type === 'nudge_complete' && nudgeActiveRef.current) {
          const beginAfterPlayback = async () => {
            await pcmPlayerRef.current.whenIdle();
            if (!nudgeActiveRef.current || mutedRef.current) return;
            nudgeActiveRef.current = false;
            await beginVoiceRef.current();
          };
          beginAfterPlayback().catch(() => undefined);
        } else if (
          event.type === 'transcript' &&
          typeof event.text === 'string'
        ) {
          setQuestion(event.text);
        } else if (event.type === 'speech_end') {
          serverSpeechEndedRef.current = true;
          recorderRef.current?.stop();
        } else if (event.type === 'llm_start') {
          setVoiceState('answering');
          // Do not open a second microphone turn while Luma is speaking.
          // Speaker output can pass the echo-cancellation threshold and be
          // mistaken for a user barge-in, which cancels the active TTS stream.
          // The next voice turn starts after playback drains in `complete`.
        } else if (
          event.type === 'answer_delta' &&
          typeof event.delta === 'string'
        ) {
          setStreamAnswer((current) => current + event.delta);
        } else if (
          event.type === 'audio_chunk' &&
          typeof event.audio === 'string' &&
          event.audio.length > 0
        ) {
          audioReceivedRef.current = true;
          pcmPlayerRef.current.enqueue(
            event.audio,
            typeof event.sample_rate === 'number' ? event.sample_rate : 24_000,
          );
        } else if (event.type === 'tts_error') {
          pcmPlayerRef.current.stop();
          audioReceivedRef.current = false;
          setVoiceError(
            `${event.message || 'ElevenLabs streaming failed'}. Using native voice.`,
          );
        } else if (event.type === 'complete') {
          const answer = typeof event.answer === 'string' ? event.answer : '';
          const playbackGeneration = playbackGenerationRef.current;
          const listenAgain = async () => {
            if (audioReceivedRef.current) {
              await pcmPlayerRef.current.whenIdle();
            }
            if (playbackGeneration !== playbackGenerationRef.current) return;
            if (
              mountedRef.current &&
              !mutedRef.current &&
              recorderRef.current
            ) {
              setVoiceState('listening');
            } else if (
              mountedRef.current &&
              !mutedRef.current &&
              !voiceTurnStartingRef.current
            ) {
              await beginVoiceRef.current();
            }
          };
          if (!audioReceivedRef.current) {
            speakAnswer(answer, () => {
              listenAgain().catch(() => undefined);
            });
          } else {
            listenAgain().catch(() => undefined);
          }
          setStreamAnswer('');
        } else if (event.type === 'voice_control') {
          setQuestion('');
          setStreamAnswer('');
          if (event.action === 'mute') {
            setMutedRef.current(true).catch(() => undefined);
          } else if (event.action === 'reselect') {
            window.electron.ipcRenderer.sendMessage('selection-retry');
          } else if (event.action === 'close') {
            window.electron.ipcRenderer.sendMessage('selection-cancel');
          }
        } else if (event.type === 'error') {
          setVoiceState('idle');
          setVoiceError(event.message || 'Voice streaming failed.');
        }
      },
    );
    window.electron.ipcRenderer.sendMessage('selection-preview-ready');
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        window.electron.ipcRenderer.sendMessage('selection-cancel');
      }
    };
    window.addEventListener('keydown', close);
    return () => {
      mountedRef.current = false;
      disposeState();
      disposeVoice();
      recorderRef.current?.cancel();
      closeVoiceRecorder();
      if (wakeWordPausedRef.current) {
        window.electron.ipcRenderer
          .invoke('set-wake-word-capture-paused', { paused: false })
          .catch(() => undefined);
      }
      stopSpeech();
      pcmPlayer.dispose();
      window.removeEventListener('keydown', close);
    };
  }, [speakAnswer, stopSpeech]);

  useEffect(() => {
    if (!state.imageDataUrl || inputPrewarmedRef.current) {
      return;
    }
    inputPrewarmedRef.current = true;
    const prewarmInput = async () => {
      const permitted = await window.electron.ipcRenderer.invoke(
        'selection-voice-permission',
      );
      if (!permitted || !mountedRef.current) return;
      await window.electron.ipcRenderer.invoke('set-wake-word-capture-paused', {
        paused: true,
      });
      wakeWordPausedRef.current = true;
      await prewarmVoiceRecorder();
    };
    prewarmInput().catch(() => undefined);
  }, [state.imageDataUrl, state.status]);

  const handleVoice = async () => {
    if (mutedRef.current || voiceTurnStartingRef.current) return;
    if (recorderRef.current) {
      recorderRef.current.stop();
      return;
    }
    stopSpeech();
    nudgeActiveRef.current = false;
    pcmPlayerRef.current.prepare();
    audioReceivedRef.current = false;
    setVoiceError('');
    setQuestion('');
    setStreamAnswer('');
    setVoiceState('requesting');
    voiceTurnStartingRef.current = true;
    serverSpeechEndedRef.current = false;
    let streamStarted = false;
    try {
      const permitted = await window.electron.ipcRenderer.invoke(
        'selection-voice-permission',
      );
      if (!permitted) {
        throw new Error(
          'Microphone access is required. Enable Electron in System Settings > Privacy & Security > Microphone.',
        );
      }
      if (mutedRef.current) throw new Error('Voice recording cancelled.');
      if (!wakeWordPausedRef.current) {
        await window.electron.ipcRenderer.invoke(
          'set-wake-word-capture-paused',
          { paused: true },
        );
        wakeWordPausedRef.current = true;
      }
      await prewarmVoiceRecorder();
      if (mutedRef.current) throw new Error('Voice recording cancelled.');
      const start = (await window.electron.ipcRenderer.invoke(
        'selection-voice-start',
      )) as { ready?: boolean; error?: string } | undefined;
      if (!start?.ready) {
        throw new Error(
          start?.error || 'Voice streaming service is unavailable.',
        );
      }
      if (mutedRef.current) throw new Error('Voice recording cancelled.');
      streamStarted = true;
      const recorder = await startVoiceRecorder({
        silenceMs: 1_500,
        maxDurationMs: 30_000,
        onPcmChunk: (pcm) =>
          window.electron.ipcRenderer.sendMessage('selection-voice-audio', pcm),
        onStatus: setVoiceState,
      });
      recorderRef.current = recorder;
      await recorder.done;
      recorderRef.current = null;
      setVoiceState('transcribing');
      if (!serverSpeechEndedRef.current) {
        window.electron.ipcRenderer.sendMessage('selection-voice-stop');
      }
    } catch (error) {
      if (streamStarted) {
        window.electron.ipcRenderer.sendMessage('selection-voice-stop');
      }
      const message = error instanceof Error ? error.message : String(error);
      if (message !== 'Voice recording cancelled.' && !mutedRef.current) {
        setVoiceError(message);
      }
      setVoiceState('idle');
    } finally {
      recorderRef.current = null;
      voiceTurnStartingRef.current = false;
    }
  };
  beginVoiceRef.current = handleVoice;

  const changeMuted = async (nextMuted: boolean) => {
    mutedRef.current = nextMuted;
    setMuted(nextMuted);
    setVoiceError('');
    setVoiceRecorderMuted(nextMuted);
    if (nextMuted) {
      return;
    }
    if (recorderRef.current || voiceTurnStartingRef.current) return;
    if (nudgeActiveRef.current) {
      await pcmPlayerRef.current.whenIdle();
      if (mutedRef.current || !nudgeActiveRef.current) return;
      nudgeActiveRef.current = false;
    }
    await beginVoiceRef.current();
  };
  setMutedRef.current = changeMuted;

  const voiceActive =
    voiceState === 'listening' ||
    voiceState === 'speaking' ||
    voiceState === 'answering';
  const voiceStatus = muted
        ? 'Microphone muted. Luma audio will keep playing.'
    : {
        requesting: 'Opening the live voice stream…',
        listening: 'Listening and transcribing live…',
        speaking: 'Listening… I’ll respond when you pause.',
        transcribing: 'Understanding what you said…',
        answering: 'Speaking — interrupt me whenever you want.',
        idle: 'Listening starts automatically. Speak naturally.',
      }[voiceState];
  const turns = state.turns ?? [];
  const isAnswering = Boolean(streamAnswer) && state.status !== 'answer';

  return (
    <main className="selection-card-shell">
      <section className="selection-card">
        <header className="selection-card__header">
          <div className="selection-card__identity">
            <span className="selection-mark" aria-hidden>
              ⌖
            </span>
            <div>
              <strong>Luma</strong>
              <span>Visual Copilot · selected pixels only</span>
            </div>
          </div>
          <div className="selection-card__header-actions">
          <button
            className="selection-card__close selection-card__settings"
            type="button"
            aria-label="Open Luma settings"
            title="Settings"
            onClick={() =>
              window.electron.ipcRenderer.sendMessage('open-chat-settings')
            }
          >
            <span aria-hidden>⚙</span>
          </button>
          <button
            className="selection-card__close"
            type="button"
            aria-label="Minimize Visual Copilot"
            title="Minimize"
            onClick={() =>
              window.electron.ipcRenderer.sendMessage('selection-minimize')
            }
          >
            <span aria-hidden>−</span>
          </button>
          <button
            className="selection-card__close"
            type="button"
            aria-label="Close Visual Copilot"
            title="Close"
            onClick={() =>
              window.electron.ipcRenderer.sendMessage('selection-cancel')
            }
          >
            <span aria-hidden>×</span>
          </button>
          </div>
        </header>

        {state.imageDataUrl && (
          <figure className="selection-preview-image">
            <img src={state.imageDataUrl} alt="Selected screen region" />
            <figcaption>Exactly this region will be sent</figcaption>
          </figure>
        )}

        {(state.status === 'preview' || state.status === 'sending') &&
          turns.length === 0 &&
          !streamAnswer && (
            <section className="selection-voice-panel" aria-live="polite">
              <span className="selection-question__label">
                I’ve got the screenshot
              </span>
              <p className="selection-context-nudge">{contextNudge}</p>
              <div
                className={`selection-live-state${
                  voiceActive ? ' selection-live-state--active' : ''
                }${muted ? ' selection-live-state--muted' : ''}`}
              >
                <span className="selection-live-state__dot" aria-hidden />
                <span>{voiceStatus}</span>
              </div>
              {question && (
                <p className="selection-voice-transcript">“{question}”</p>
              )}
              {voiceError && (
                <div className="selection-voice-error">{voiceError}</div>
              )}
            </section>
          )}

        {(turns.length > 0 || streamAnswer || state.status === 'answer') && (
          <article className="selection-answer" aria-live="polite">
            <span className="selection-answer__eyebrow">
              {isAnswering ? 'Answering…' : 'Conversation'}
            </span>
            <div className="selection-conversation">
              {turns.map((turn) => (
                <section
                  className={`selection-conversation__turn selection-conversation__turn--${turn.role}`}
                  key={`${turn.role}-${turn.text}`}
                >
                  <span>{turn.role === 'user' ? 'You' : 'Visual Copilot'}</span>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {turn.text}
                  </ReactMarkdown>
                </section>
              ))}
              {state.status === 'sending' && question && (
                <section className="selection-conversation__turn selection-conversation__turn--user">
                  <span>You</span>
                  <p>{question}</p>
                </section>
              )}
              {streamAnswer && (
                <section className="selection-conversation__turn selection-conversation__turn--assistant">
                  <span>Visual Copilot</span>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {streamAnswer}
                  </ReactMarkdown>
                </section>
              )}
            </div>
            <div
              className={`selection-live-state selection-live-state--conversation${
                voiceActive ? ' selection-live-state--active' : ''
              }${muted ? ' selection-live-state--muted' : ''}`}
            >
              <span className="selection-live-state__dot" aria-hidden />
              <span>{voiceStatus}</span>
            </div>
            {voiceError && (
              <p className="selection-voice-error">{voiceError}</p>
            )}
          </article>
        )}

        {state.status === 'error' && (
          <div className="selection-error" role="alert">
            <span>Couldn’t complete that request</span>
            <p>{state.error}</p>
          </div>
        )}

        <footer className="selection-voice-controls">
          <button
            className={`selection-button selection-button--quiet selection-mute-button${
              muted ? ' selection-mute-button--muted' : ''
            }`}
            type="button"
            aria-pressed={muted}
            aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
            title={muted ? 'Unmute microphone' : 'Mute microphone'}
            onClick={() => changeMuted(!muted)}
          >
            <span className="selection-mute-button__icon" aria-hidden>
              {muted ? '○' : '●'}
            </span>
            {muted ? 'Unmute' : 'Mute'}
          </button>
          <button
            className="selection-button selection-button--primary"
            type="button"
            onClick={() =>
              window.electron.ipcRenderer.sendMessage('selection-retry')
            }
          >
            Select another area
          </button>
        </footer>
      </section>
    </main>
  );
}
