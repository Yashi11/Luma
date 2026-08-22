import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import PcmStreamPlayer from '../pcm-stream-player';
import {
  closeVoiceRecorder,
  prewarmVoiceRecorder,
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
  text?: string;
  delta?: string;
  audio?: string;
  answer?: string;
  message?: string;
  sample_rate?: number;
};

const CONTEXT_NUDGE = 'Got it. Want to add any context before I explain it?';

export default function SelectionPreviewView() {
  const [state, setState] = useState<PreviewState>({ status: 'preview' });
  const [question, setQuestion] = useState('');
  const [streamAnswer, setStreamAnswer] = useState('');
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [voiceError, setVoiceError] = useState('');
  const recorderRef = useRef<ActiveVoiceRecorder | null>(null);
  const pcmPlayerRef = useRef(new PcmStreamPlayer());
  const audioReceivedRef = useRef(false);
  const nudgeActiveRef = useRef(true);
  const beginVoiceRef = useRef<() => Promise<void>>(async () => {});
  const wakeWordPausedRef = useRef(false);
  const voiceTurnStartingRef = useRef(false);
  const serverSpeechEndedRef = useRef(false);
  const mountedRef = useRef(true);

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
        if (
          event.type === 'nudge_audio_chunk' &&
          typeof event.audio === 'string' &&
          nudgeActiveRef.current
        ) {
          pcmPlayerRef.current.enqueue(
            event.audio,
            typeof event.sample_rate === 'number' ? event.sample_rate : 24_000,
          );
        } else if (event.type === 'nudge_error' && nudgeActiveRef.current) {
          speakAnswer(CONTEXT_NUDGE, () => {
            if (!nudgeActiveRef.current) return;
            nudgeActiveRef.current = false;
            beginVoiceRef.current().catch(() => undefined);
          });
        } else if (event.type === 'nudge_complete' && nudgeActiveRef.current) {
          const beginAfterPlayback = async () => {
            await pcmPlayerRef.current.whenIdle();
            if (!nudgeActiveRef.current) return;
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
        } else if (
          event.type === 'answer_delta' &&
          typeof event.delta === 'string'
        ) {
          setStreamAnswer((current) => current + event.delta);
        } else if (
          event.type === 'audio_chunk' &&
          typeof event.audio === 'string'
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
          setVoiceState('idle');
          const answer = typeof event.answer === 'string' ? event.answer : '';
          const listenAgain = async () => {
            if (audioReceivedRef.current) {
              await pcmPlayerRef.current.whenIdle();
            }
            if (
              mountedRef.current &&
              !recorderRef.current &&
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
        } else if (event.type === 'error') {
          setVoiceState('idle');
          setVoiceError(event.message || 'Voice streaming failed.');
        }
      },
    );
    window.electron.ipcRenderer.sendMessage('selection-preview-ready');
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

  const handleVoice = async () => {
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
      if (!wakeWordPausedRef.current) {
        await window.electron.ipcRenderer.invoke(
          'set-wake-word-capture-paused',
          { paused: true },
        );
        wakeWordPausedRef.current = true;
      }
      await prewarmVoiceRecorder();
      const start = (await window.electron.ipcRenderer.invoke(
        'selection-voice-start',
      )) as { ready?: boolean; error?: string } | undefined;
      if (!start?.ready) {
        throw new Error(
          start?.error || 'Voice streaming service is unavailable.',
        );
      }
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
      if (message !== 'Voice recording cancelled.') setVoiceError(message);
      setVoiceState('idle');
    } finally {
      recorderRef.current = null;
      voiceTurnStartingRef.current = false;
    }
  };
  beginVoiceRef.current = handleVoice;

  const voiceActive = voiceState === 'listening' || voiceState === 'speaking';
  const voiceStatus = {
    requesting: 'Opening the live voice stream…',
    listening: 'Listening and transcribing live…',
    speaking: 'Listening… I’ll send when you pause.',
    transcribing: 'Deepgram is finalizing your question…',
    answering: 'Answering and speaking as the response arrives…',
    idle: 'Tap the microphone and ask about this selection.',
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
              <strong>Visual Copilot</strong>
              <span>Live voice · selected pixels only</span>
            </div>
          </div>
          <button
            className="selection-icon-button"
            type="button"
            aria-label="Close"
            onClick={() =>
              window.electron.ipcRenderer.sendMessage('selection-cancel')
            }
          >
            ×
          </button>
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
              <p className="selection-context-nudge">{CONTEXT_NUDGE}</p>
              <button
                className={`selection-voice-button selection-voice-button--primary${
                  voiceActive ? ' selection-voice-button--active' : ''
                }`}
                type="button"
                aria-label={
                  voiceActive
                    ? 'Stop and send voice question'
                    : 'Start voice question'
                }
                disabled={
                  state.status === 'sending' ||
                  voiceState === 'requesting' ||
                  voiceState === 'transcribing' ||
                  voiceState === 'answering'
                }
                onClick={() => handleVoice()}
              >
                {voiceActive ? (
                  <span className="selection-voice-stop" />
                ) : (
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    width="32"
                    height="32"
                  >
                    <path
                      d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Z"
                      fill="currentColor"
                    />
                    <path
                      d="M6.75 11.5a.75.75 0 0 1 1.5 0 3.75 3.75 0 0 0 7.5 0 .75.75 0 0 1 1.5 0 5.25 5.25 0 0 1-4.5 5.2V19h2a.75.75 0 0 1 0 1.5h-5.5a.75.75 0 0 1 0-1.5h2v-2.3a5.25 5.25 0 0 1-4.5-5.2Z"
                      fill="currentColor"
                    />
                  </svg>
                )}
              </button>
              <div className="selection-voice-status">{voiceStatus}</div>
              {question && (
                <p className="selection-voice-transcript">“{question}”</p>
              )}
              {voiceError && (
                <div className="selection-voice-error">{voiceError}</div>
              )}
              <button
                className="selection-reselect-link"
                type="button"
                disabled={voiceState !== 'idle'}
                onClick={() =>
                  window.electron.ipcRenderer.sendMessage('selection-retry')
                }
              >
                Reselect screen area
              </button>
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
            {voiceState !== 'idle' && (
              <p className="selection-conversation__status">{voiceStatus}</p>
            )}
            {voiceError && (
              <p className="selection-voice-error">{voiceError}</p>
            )}
            {state.status === 'answer' && (
              <div className="selection-actions selection-actions--conversation">
                <button
                  className="selection-follow-up-button"
                  type="button"
                  aria-label="Ask a follow-up by voice"
                  disabled={voiceState !== 'idle'}
                  onClick={() => handleVoice()}
                >
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    width="20"
                    height="20"
                  >
                    <path
                      d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Z"
                      fill="currentColor"
                    />
                    <path
                      d="M6.75 11.5a.75.75 0 0 1 1.5 0 3.75 3.75 0 0 0 7.5 0 .75.75 0 0 1 1.5 0 5.25 5.25 0 0 1-4.5 5.2V19h2a.75.75 0 0 1 0 1.5h-5.5a.75.75 0 0 1 0-1.5h2v-2.3a5.25 5.25 0 0 1-4.5-5.2Z"
                      fill="currentColor"
                    />
                  </svg>
                  Ask another question
                </button>
                <button
                  className="selection-button selection-button--quiet"
                  type="button"
                  onClick={() =>
                    window.electron.ipcRenderer.sendMessage('selection-cancel')
                  }
                >
                  Done
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
              </div>
            )}
          </article>
        )}

        {state.status === 'error' && (
          <div className="selection-error" role="alert">
            <span>Couldn’t complete that request</span>
            <p>{state.error}</p>
            <div className="selection-actions">
              <button
                className="selection-button selection-button--quiet"
                type="button"
                onClick={() =>
                  window.electron.ipcRenderer.sendMessage('selection-cancel')
                }
              >
                Close
              </button>
              <button
                className="selection-button selection-button--primary"
                type="button"
                onClick={() =>
                  window.electron.ipcRenderer.sendMessage('selection-retry')
                }
              >
                Try another selection
              </button>
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
