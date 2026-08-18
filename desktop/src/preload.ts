import { contextBridge, ipcRenderer } from 'electron';
import type { Explanation, PreviewPayload, SelectionResult } from './shared';

contextBridge.exposeInMainWorld('visualCopilot', {
  completeSelection: (selection: SelectionResult) => ipcRenderer.send('selection:complete', selection),
  cancel: () => ipcRenderer.send('selection:cancel'),
  submitQuestion: (captureId: string, question: string) => ipcRenderer.invoke('preview:submit', { captureId, question }) as Promise<Explanation>,
  cancelPreview: (captureId: string) => ipcRenderer.send('preview:cancel', captureId),
  retrySelection: () => ipcRenderer.send('selection:retry'),
  onPreview: (listener: (payload: PreviewPayload) => void) => ipcRenderer.on('preview:ready', (_event, payload) => listener(payload)),
  onError: (listener: (message: string) => void) => ipcRenderer.on('selection:error', (_event, message) => listener(message)),
});
