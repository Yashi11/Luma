import { FormEvent, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './SelectionSurface.css';

type PreviewState = {
  imageDataUrl?: string;
  status: 'preview' | 'sending' | 'answer' | 'error';
  answer?: string;
  uncertainty?: string | null;
  needsMoreContext?: boolean;
  error?: string;
};

export default function SelectionPreviewView() {
  const [state, setState] = useState<PreviewState>({ status: 'preview' });
  const [question, setQuestion] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const dispose = window.electron.ipcRenderer.on(
      'selection-preview-state',
      (next) => setState(next as PreviewState),
    );
    window.electron.ipcRenderer.sendMessage('selection-preview-ready');
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        window.electron.ipcRenderer.sendMessage('selection-cancel');
      }
    };
    window.addEventListener('keydown', close);
    return () => {
      dispose();
      window.removeEventListener('keydown', close);
    };
  }, []);

  useEffect(() => {
    if (state.status === 'preview') inputRef.current?.focus();
  }, [state.status]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    window.electron.ipcRenderer.sendMessage(
      'selection-preview-submit',
      question,
    );
  };

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
              <span>OpenAI · selected pixels only</span>
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

        {(state.status === 'preview' || state.status === 'sending') && (
          <form className="selection-question" onSubmit={submit}>
            <span className="selection-question__label">
              What would you like to know?
            </span>
            <textarea
              ref={inputRef}
              id="selection-question"
              value={question}
              maxLength={4000}
              rows={3}
              placeholder="Explain this…"
              disabled={state.status === 'sending'}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <div className="selection-actions">
              <button
                className="selection-button selection-button--quiet"
                type="button"
                disabled={state.status === 'sending'}
                onClick={() =>
                  window.electron.ipcRenderer.sendMessage('selection-retry')
                }
              >
                Reselect
              </button>
              <button
                className="selection-button selection-button--primary"
                type="submit"
                disabled={state.status === 'sending'}
              >
                {state.status === 'sending'
                  ? 'Reading selection…'
                  : 'Ask Copilot'}
              </button>
            </div>
          </form>
        )}

        {state.status === 'answer' && (
          <article className="selection-answer" aria-live="polite">
            <span className="selection-answer__eyebrow">Answer</span>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {state.answer || ''}
            </ReactMarkdown>
            {state.uncertainty && (
              <p className="selection-note">
                <strong>Uncertainty:</strong> {state.uncertainty}
              </p>
            )}
            {state.needsMoreContext && (
              <p className="selection-note">
                This selection needs a little more context.
              </p>
            )}
            <div className="selection-actions">
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
