import { randomBytes } from 'crypto';
import axios, { AxiosInstance } from 'axios';
import {
  BrowserWindow,
  desktopCapturer,
  Display,
  ipcMain,
  screen,
  systemPreferences,
} from 'electron';
import log from 'electron-log';
import { serviceManager } from './services/manager';

/* eslint-disable no-await-in-loop -- startup readiness polling is intentionally sequential */

type RectangleSelection = {
  type: 'rectangle';
  x: number;
  y: number;
  width: number;
  height: number;
};

type FreeformSelection = {
  type: 'freeform';
  x: number;
  y: number;
  width: number;
  height: number;
  points: Array<{ x: number; y: number }>;
};

type Selection = RectangleSelection | FreeformSelection;

type PreviewState = {
  imageDataUrl?: string;
  status: 'preview' | 'sending' | 'answer' | 'error';
  answer?: string;
  uncertainty?: string | null;
  needsMoreContext?: boolean;
  error?: string;
  turns?: Array<{ role: 'user' | 'assistant'; text: string }>;
};

type VoiceEvent = {
  type: string;
  action?: 'mute' | 'reselect' | 'close';
  text?: string;
  transcript?: string;
  delta?: string;
  audio?: string;
  answer?: string;
  message?: string;
  sample_rate?: number;
};

type Crop = {
  x: number;
  y: number;
  width: number;
  height: number;
};

const displayPayload = (display: Display) => {
  const allDisplays = screen.getAllDisplays();
  const configurationId = allDisplays
    .map((item) => [item.id, item.bounds, item.scaleFactor, item.rotation])
    .sort((left, right) => Number(left[0]) - Number(right[0]))
    .map((item) => JSON.stringify(item))
    .join('|');
  return {
    display_id: String(display.id),
    dip_width: display.bounds.width,
    dip_height: display.bounds.height,
    dip_left: display.bounds.x,
    dip_top: display.bounds.y,
    capture_width: Math.round(display.bounds.width * display.scaleFactor),
    capture_height: Math.round(display.bounds.height * display.scaleFactor),
    capture_left: Math.round(display.bounds.x * display.scaleFactor),
    capture_top: Math.round(display.bounds.y * display.scaleFactor),
    rotation_degrees: display.rotation,
    configuration_id: configurationId,
  };
};

const SELECTION_REQUEST_TIMEOUT_MS = 10_000;

export default class SelectionController {
  private readonly api: AxiosInstance;

  private overlayWindow: BrowserWindow | null = null;

  private previewWindow: BrowserWindow | null = null;

  private previewShouldBeVisible = false;

  private activationInProgress = false;

  private previewState: PreviewState = { status: 'preview' };

  private sessionId: string | null = null;

  private activeDisplayId: number | null = null;

  private voiceSocket: WebSocket | null = null;

  private nudgeSocket: WebSocket | null = null;

  private nudgedSessionId: string | null = null;

  private streamedAnswer = '';

  private streamedQuestion = '';

  constructor(
    private readonly port: number,
    private readonly token: string,
    private readonly preloadPath: () => string,
    private readonly resolveHtmlPath: (htmlFileName: string) => string,
    private readonly onOverlayVisibilityChange: (
      visible: boolean,
    ) => void = () => undefined,
  ) {
    this.api = axios.create({
      baseURL: `http://127.0.0.1:${port}`,
      headers: { Authorization: `Bearer ${token}` },
      timeout: 35_000,
    });
    this.registerIpc();
  }

  static createToken(): string {
    return randomBytes(32).toString('base64url');
  }

  startService(): void {
    serviceManager.configureServiceArg(
      'visual-copilot-server',
      'port',
      String(this.port),
    );
    serviceManager.configureServiceEnv(
      'visual-copilot-server',
      {
        VISUAL_COPILOT_CAPABILITY_TOKEN: this.token,
        OPENAI_MODEL: process.env.OPENAI_MODEL?.trim() || 'gpt-5.6-sol',
        ...(process.env.OPENAI_API_KEY?.trim()
          ? { OPENAI_API_KEY: process.env.OPENAI_API_KEY.trim() }
          : {}),
        ...(process.env.DEEPGRAM_API_KEY?.trim()
          ? { DEEPGRAM_API_KEY: process.env.DEEPGRAM_API_KEY.trim() }
          : {}),
        ...(process.env.ELEVENLABS_API_KEY?.trim()
          ? { ELEVENLABS_API_KEY: process.env.ELEVENLABS_API_KEY.trim() }
          : {}),
        ...(process.env.ELEVEN_LABS_VOICE_ID?.trim()
          ? { ELEVEN_LABS_VOICE_ID: process.env.ELEVEN_LABS_VOICE_ID.trim() }
          : {}),
        VISUAL_COPILOT_CAPTURE_MODE: 'electron',
      },
      true,
    );
    serviceManager.startService('visual-copilot-server');
  }

  async activate(): Promise<void> {
    if (this.activationInProgress) {
      log.info('[Visual Copilot] Ignoring duplicate activation');
      return;
    }
    this.activationInProgress = true;
    try {
      await this.cancel();
      this.previewState = { status: 'preview' };
      const cursor = screen.getCursorScreenPoint();
      const display = screen.getDisplayNearestPoint(cursor);
      this.activeDisplayId = display.id;
      log.info(
        `[Visual Copilot] Active Electron display id=${display.id} ` +
          `bounds=${JSON.stringify(display.bounds)} scale=${display.scaleFactor} ` +
          `cursor=${JSON.stringify(cursor)}`,
      );
      await this.waitUntilReady();
      const response = await this.api.post('/activate', {
        display: displayPayload(display),
      });
      this.sessionId = response.data.session_id;
      this.createOverlay(display);
    } catch (error) {
      this.showError(SelectionController.messageFor(error));
    } finally {
      this.activationInProgress = false;
    }
  }

  showPreviewIfAvailable(): boolean {
    const hasCapturedSession = Boolean(
      this.sessionId && this.previewState.imageDataUrl,
    );
    const hasPendingError = this.previewState.status === 'error';
    if (!hasCapturedSession && !hasPendingError) return false;

    const display =
      screen
        .getAllDisplays()
        .find((item) => item.id === this.activeDisplayId) ??
      screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
    this.previewShouldBeVisible = true;
    if (!this.previewWindow) this.createPreview(display, true);
    else {
      this.publishPreview();
      if (!this.previewWindow.webContents.isLoadingMainFrame()) {
        this.previewWindow.show();
        this.previewWindow.focus();
      }
    }
    return true;
  }

  isPreviewVisible(): boolean {
    return Boolean(
      this.previewWindow &&
        !this.previewWindow.isDestroyed() &&
        this.previewWindow.isVisible(),
    );
  }

  minimizePreview(): void {
    if (!this.previewWindow || this.previewWindow.isDestroyed()) return;
    this.previewShouldBeVisible = false;
    this.previewWindow.hide();
  }

  private async waitUntilReady(): Promise<void> {
    let lastError: unknown;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      try {
        await this.api.get('/health', { timeout: 750 });
        return;
      } catch (error) {
        lastError = error;
        await new Promise((resolve) => {
          setTimeout(resolve, 200);
        });
      }
    }
    throw lastError ?? new Error('selection service did not start');
  }

  private createOverlay(display: Display): void {
    this.overlayWindow = new BrowserWindow({
      ...display.bounds,
      type: process.platform === 'darwin' ? 'panel' : undefined,
      show: false,
      frame: false,
      transparent: true,
      resizable: false,
      movable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      fullscreenable: false,
      hasShadow: false,
      webPreferences: { preload: this.preloadPath() },
    });
    this.overlayWindow.setAlwaysOnTop(true, 'screen-saver');
    this.overlayWindow.setVisibleOnAllWorkspaces(true, {
      visibleOnFullScreen: true,
      skipTransformProcessType: process.platform === 'darwin',
    });
    this.overlayWindow
      .loadURL(`${this.resolveHtmlPath('index.html')}?view=selection-overlay`)
      .catch((error) => {
        log.error('[Visual Copilot] overlay load failed', error);
        this.destroyOverlay();
        this.showError('The selection overlay could not be opened.');
      });
    this.overlayWindow.webContents.on('before-input-event', (_event, input) => {
      if (input.type === 'keyDown' && input.key === 'Escape') {
        this.cancel().catch((error) =>
          log.warn('[Visual Copilot] Escape cancellation failed', error),
        );
      }
    });
    this.overlayWindow.once('ready-to-show', () => {
      this.onOverlayVisibilityChange(true);
      this.overlayWindow?.show();
      this.overlayWindow?.focus();
    });
    this.overlayWindow.on('closed', () => {
      this.overlayWindow = null;
      this.onOverlayVisibilityChange(false);
    });
  }

  private createPreview(display: Display, showWhenReady = true): void {
    const width = 430;
    const height = Math.min(650, display.workArea.height - 32);
    this.previewShouldBeVisible = showWhenReady;
    this.previewWindow = new BrowserWindow({
      show: false,
      width,
      height,
      x: display.workArea.x + display.workArea.width - width - 18,
      y: display.workArea.y + 16,
      minWidth: 380,
      minHeight: 480,
      frame: false,
      transparent: true,
      resizable: true,
      alwaysOnTop: true,
      skipTaskbar: true,
      webPreferences: {
        preload: this.preloadPath(),
        partition: 'visual-copilot-selection',
        backgroundThrottling: false,
      },
    });
    const previewSession = this.previewWindow.webContents.session;
    previewSession.setPermissionCheckHandler(
      (webContents, permission, _origin, details) =>
        webContents === this.previewWindow?.webContents &&
        permission === 'media' &&
        details.mediaType === 'audio',
    );
    previewSession.setPermissionRequestHandler(
      (webContents, permission, callback, details) => {
        const mediaTypes =
          'mediaTypes' in details && Array.isArray(details.mediaTypes)
            ? details.mediaTypes
            : [];
        callback(
          webContents === this.previewWindow?.webContents &&
            permission === 'media' &&
            mediaTypes.includes('audio') &&
            !mediaTypes.includes('video'),
        );
      },
    );
    this.previewWindow.setAlwaysOnTop(true, 'floating');
    this.previewWindow.setVisibleOnAllWorkspaces(true, {
      visibleOnFullScreen: true,
      skipTransformProcessType: process.platform === 'darwin',
    });
    this.previewWindow
      .loadURL(`${this.resolveHtmlPath('index.html')}?view=selection-preview`)
      .catch((error) =>
        log.error('[Visual Copilot] preview load failed', error),
      );
    this.previewWindow.once('ready-to-show', () => {
      if (this.previewShouldBeVisible) {
        this.previewWindow?.show();
        this.previewWindow?.focus();
      }
    });
    this.previewWindow.on('closed', () => {
      this.previewWindow = null;
      this.previewShouldBeVisible = false;
    });
  }

  private publishPreview(): void {
    this.previewWindow?.webContents.send(
      'selection-preview-state',
      this.previewState,
    );
  }

  private registerIpc(): void {
    ipcMain.removeAllListeners('selection-complete');
    ipcMain.on('selection-complete', (event, selection: Selection) => {
      if (event.sender !== this.overlayWindow?.webContents) return;
      this.completeSelection(selection).catch((error) =>
        this.showError(SelectionController.messageFor(error)),
      );
    });
    ipcMain.removeAllListeners('selection-cancel');
    ipcMain.on('selection-cancel', (event) => {
      if (
        event.sender !== this.overlayWindow?.webContents &&
        event.sender !== this.previewWindow?.webContents
      )
        return;
      this.cancel().catch((error) =>
        log.warn('[Visual Copilot] cancel failed', error),
      );
    });
    ipcMain.removeAllListeners('selection-minimize');
    ipcMain.on('selection-minimize', (event) => {
      if (event.sender !== this.previewWindow?.webContents) return;
      this.minimizePreview();
    });
    ipcMain.removeAllListeners('selection-preview-ready');
    ipcMain.on('selection-preview-ready', (event) => {
      if (event.sender === this.previewWindow?.webContents) {
        this.publishPreview();
        this.startNudgeStream();
      }
    });
    ipcMain.removeHandler('selection-voice-start');
    ipcMain.handle('selection-voice-start', async (event) => {
      if (event.sender !== this.previewWindow?.webContents) {
        return { error: 'Voice question is not available.' };
      }
      return this.startVoiceStream();
    });
    ipcMain.removeAllListeners('selection-voice-audio');
    ipcMain.on('selection-voice-audio', (event, audio: unknown) => {
      if (
        event.sender !== this.previewWindow?.webContents ||
        this.voiceSocket?.readyState !== WebSocket.OPEN ||
        typeof audio !== 'string' ||
        audio.length > 262_144 ||
        !/^[A-Za-z0-9+/]*={0,2}$/.test(audio)
      )
        return;
      const pcm = Buffer.from(audio, 'base64');
      if (!pcm.length || pcm.length % 2) return;
      this.voiceSocket.send(pcm);
    });
    ipcMain.removeAllListeners('selection-voice-stop');
    ipcMain.on('selection-voice-stop', (event) => {
      if (
        event.sender === this.previewWindow?.webContents &&
        this.voiceSocket?.readyState === WebSocket.OPEN
      ) {
        this.voiceSocket.send(JSON.stringify({ type: 'stop' }));
      }
    });
    ipcMain.removeAllListeners('selection-voice-cancel');
    ipcMain.on('selection-voice-cancel', (event) => {
      if (event.sender === this.previewWindow?.webContents) {
        this.closeVoiceSocket();
      }
    });
    ipcMain.removeHandler('selection-voice-permission');
    ipcMain.handle('selection-voice-permission', async (event) => {
      if (event.sender !== this.previewWindow?.webContents) return false;
      if (process.platform !== 'darwin') return true;
      if (systemPreferences.getMediaAccessStatus('microphone') === 'granted')
        return true;
      return systemPreferences.askForMediaAccess('microphone');
    });
    ipcMain.removeAllListeners('selection-retry');
    ipcMain.on('selection-retry', (event) => {
      if (event.sender !== this.previewWindow?.webContents) return;
      this.cancel(false)
        .then(() => this.activate())
        .catch((error) =>
          this.showError(SelectionController.messageFor(error)),
        );
    });
  }

  private async completeSelection(selection: Selection): Promise<void> {
    if (!this.sessionId || this.activeDisplayId == null) return;
    const { sessionId } = this;
    try {
      const frozen = await this.api.post(
        `/sessions/${sessionId}/freeze`,
        { selection },
        { timeout: SELECTION_REQUEST_TIMEOUT_MS },
      );
      const crop = frozen.data?.crop_px as Crop | undefined;
      if (
        !crop ||
        ![crop.x, crop.y, crop.width, crop.height].every(Number.isFinite)
      )
        throw new Error('Selection service returned invalid crop geometry.');
      this.overlayWindow?.hide();
      await new Promise((resolve) => {
        setTimeout(resolve, 60);
      });
      await this.api.post(`/sessions/${sessionId}/overlay-hidden`, undefined, {
        timeout: SELECTION_REQUEST_TIMEOUT_MS,
      });
      const display = screen
        .getAllDisplays()
        .find((item) => item.id === this.activeDisplayId);
      if (!display)
        throw new Error('Display configuration changed; select again.');
      const imageData = await SelectionController.captureSelectedPixels(
        display,
        selection,
        crop,
      );
      const capture = await this.api.post(
        `/sessions/${sessionId}/capture`,
        {
          display: displayPayload(display),
          image_data: imageData,
        },
        { timeout: SELECTION_REQUEST_TIMEOUT_MS },
      );
      this.previewState = {
        status: 'preview',
        imageDataUrl: capture.data.image_data_url,
      };
      this.createPreview(display, false);
      log.info(
        '[Visual Copilot] Capture ready; voice process started with panel hidden',
      );
    } catch (error) {
      this.showError(SelectionController.messageFor(error));
    } finally {
      // The overlay owns the user's mouse and keyboard surface. It must never
      // survive a failed or timed-out capture operation.
      this.destroyOverlay();
    }
  }

  private destroyOverlay(): void {
    const overlay = this.overlayWindow;
    if (!overlay || overlay.isDestroyed()) {
      this.overlayWindow = null;
      this.onOverlayVisibilityChange(false);
      return;
    }
    overlay.destroy();
  }

  private static async captureSelectedPixels(
    display: Display,
    selection: Selection,
    expectedCrop: Crop,
  ): Promise<string> {
    const targetSize = {
      width: Math.round(display.bounds.width * display.scaleFactor),
      height: Math.round(display.bounds.height * display.scaleFactor),
    };
    let sources;
    try {
      sources = await desktopCapturer.getSources({
        types: ['screen'],
        thumbnailSize: targetSize,
        fetchWindowIcons: false,
      });
    } catch (error) {
      log.warn('[Visual Copilot] Electron screen capture failed', error);
      throw new Error(
        'macOS blocked screen capture for the app that launched Coco. Fully quit Coco and start it again; in development, use npm start so Electron launches with its own Screen Recording permission.',
      );
    }
    const source = sources.find(
      (item) =>
        item.display_id === String(display.id) ||
        item.id === `screen:${display.id}:0`,
    );
    if (!source || source.thumbnail.isEmpty()) {
      throw new Error(
        'Electron could not capture this display. Enable Electron in System Settings > Privacy & Security > Screen & System Audio Recording, fully quit Electron, then start Coco again.',
      );
    }

    const sourceSize = source.thumbnail.getSize();
    const sourceCrop = {
      x: Math.max(
        0,
        Math.floor((selection.x / display.bounds.width) * sourceSize.width),
      ),
      y: Math.max(
        0,
        Math.floor((selection.y / display.bounds.height) * sourceSize.height),
      ),
      width: Math.max(
        1,
        Math.ceil((selection.width / display.bounds.width) * sourceSize.width),
      ),
      height: Math.max(
        1,
        Math.ceil(
          (selection.height / display.bounds.height) * sourceSize.height,
        ),
      ),
    };
    sourceCrop.width = Math.min(
      sourceCrop.width,
      sourceSize.width - sourceCrop.x,
    );
    sourceCrop.height = Math.min(
      sourceCrop.height,
      sourceSize.height - sourceCrop.y,
    );
    let selected = source.thumbnail.crop(sourceCrop);
    if (
      selected.getSize().width !== expectedCrop.width ||
      selected.getSize().height !== expectedCrop.height
    ) {
      selected = selected.resize({
        width: expectedCrop.width,
        height: expectedCrop.height,
        quality: 'best',
      });
    }
    const png = selected.toPNG();
    if (!png.length) throw new Error('The selected screen region was empty.');
    log.info(
      `[Visual Copilot] Captured display id=${display.id} ` +
        `frame=${sourceSize.width}x${sourceSize.height} ` +
        `crop=${JSON.stringify(sourceCrop)}`,
    );
    return png.toString('base64');
  }

  private async startVoiceStream(): Promise<{
    ready?: boolean;
    error?: string;
  }> {
    if (
      !this.sessionId ||
      !['preview', 'sending', 'answer'].includes(this.previewState.status)
    ) {
      return { error: 'Voice question is not available for this selection.' };
    }
    if (this.previewState.status === 'sending') {
      log.info('[Visual Copilot] Starting barge-in voice turn');
    }
    this.closeVoiceSocket();
    this.closeNudgeSocket();
    this.streamedAnswer = '';
    this.streamedQuestion = '';
    const { sessionId } = this;
    const socket = new WebSocket(
      `ws://127.0.0.1:${this.port}/sessions/${sessionId}/voice`,
      [`vc.${this.token}`],
    );
    this.voiceSocket = socket;
    return new Promise((resolve) => {
      let settled = false;
      let timeout: ReturnType<typeof setTimeout>;
      const finish = (result: { ready?: boolean; error?: string }) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        resolve(result);
      };
      timeout = setTimeout(() => {
        finish({ error: 'Voice streaming service did not become ready.' });
        socket.close();
      }, 15_000);
      socket.onmessage = (message) => {
        if (this.voiceSocket !== socket) return;
        try {
          const voiceEvent = JSON.parse(String(message.data)) as VoiceEvent;
          if (voiceEvent.type === 'ready') finish({ ready: true });
          if (voiceEvent.type === 'error') {
            finish({ error: voiceEvent.message || 'Voice streaming failed.' });
          }
          this.handleVoiceEvent(voiceEvent);
        } catch (error) {
          log.warn('[Visual Copilot] invalid voice stream event', error);
        }
      };
      socket.onerror = () => {
        finish({ error: 'Voice streaming service is unavailable.' });
      };
      socket.onclose = () => {
        if (this.voiceSocket === socket) this.voiceSocket = null;
        finish({ error: 'Voice streaming connection closed.' });
      };
    });
  }

  private handleVoiceEvent(event: VoiceEvent): void {
    if (event.type === 'transcript' && typeof event.text === 'string') {
      this.streamedQuestion = event.text.trim();
    } else if (event.type === 'llm_start') {
      this.previewState = {
        ...this.previewState,
        status: 'sending',
        error: undefined,
      };
      this.publishPreview();
    } else if (
      event.type === 'answer_delta' &&
      typeof event.delta === 'string'
    ) {
      this.streamedAnswer += event.delta;
    } else if (event.type === 'complete') {
      const answer =
        typeof event.answer === 'string' && event.answer.trim()
          ? event.answer.trim()
          : this.streamedAnswer.trim();
      this.previewState = {
        ...this.previewState,
        status: 'answer',
        answer,
        turns: [
          ...(this.previewState.turns ?? []),
          ...(this.streamedQuestion
            ? [
                {
                  role: 'user' as const,
                  text: this.streamedQuestion,
                },
              ]
            : []),
          { role: 'assistant', text: answer },
        ],
        uncertainty: null,
        needsMoreContext: false,
      };
      this.publishPreview();
    } else if (event.type === 'voice_control') {
      this.previewState = {
        ...this.previewState,
        status: this.previewState.turns?.length ? 'answer' : 'preview',
      };
      this.publishPreview();
    } else if (event.type === 'error') {
      this.previewState = {
        ...this.previewState,
        status: this.previewState.turns?.length ? 'answer' : 'preview',
      };
      this.publishPreview();
    }
    this.previewWindow?.webContents.send('selection-voice-event', event);
  }

  private startNudgeStream(): void {
    if (
      !this.sessionId ||
      this.previewState.status !== 'preview' ||
      this.nudgedSessionId === this.sessionId
    )
      return;
    this.closeNudgeSocket();
    this.nudgedSessionId = this.sessionId;
    const socket = new WebSocket(
      `ws://127.0.0.1:${this.port}/sessions/${this.sessionId}/nudge`,
      [`vc.${this.token}`],
    );
    this.nudgeSocket = socket;
    socket.onmessage = (message) => {
      try {
        const voiceEvent = JSON.parse(String(message.data)) as VoiceEvent;
        this.previewWindow?.webContents.send(
          'selection-voice-event',
          voiceEvent,
        );
      } catch (error) {
        log.warn('[Visual Copilot] invalid context nudge event', error);
      }
    };
    socket.onerror = () => {
      log.warn('[Visual Copilot] context nudge stream is unavailable');
    };
    socket.onclose = () => {
      if (this.nudgeSocket === socket) this.nudgeSocket = null;
    };
  }

  private showError(message: string): void {
    log.error(`[Visual Copilot] ${message}`);
    this.previewState = { status: 'error', error: message };
    if (this.previewWindow) this.publishPreview();
  }

  private static messageFor(error: unknown): string {
    if (axios.isAxiosError(error)) {
      const detail = error.response?.data?.detail;
      if (typeof detail === 'string') return detail;
      if (error.code === 'ECONNREFUSED')
        return 'Selection service is unavailable.';
    }
    if (typeof error === 'string' && error.trim()) return error;
    if (
      error &&
      typeof error === 'object' &&
      'message' in error &&
      typeof error.message === 'string' &&
      error.message.trim()
    )
      return error.message;
    return error instanceof Error
      ? error.message
      : 'Visual Copilot request failed.';
  }

  private async cancel(closePreview = true): Promise<void> {
    this.closeVoiceSocket();
    this.closeNudgeSocket();
    const { sessionId } = this;
    this.sessionId = null;
    this.previewState = { status: 'preview' };
    this.previewShouldBeVisible = false;
    // Release the full-screen input surface before contacting the service.
    // A stalled backend must never make the user's desktop unusable.
    this.destroyOverlay();
    if (closePreview) this.previewWindow?.destroy();
    else {
      this.previewWindow?.destroy();
      this.previewWindow = null;
    }
    if (sessionId) {
      this.api
        .post(`/sessions/${sessionId}/cancel`, undefined, {
          timeout: SELECTION_REQUEST_TIMEOUT_MS,
        })
        .catch((error) =>
          log.debug('[Visual Copilot] session cancellation failed', error),
        );
    }
  }

  private closeVoiceSocket(): void {
    const socket = this.voiceSocket;
    this.voiceSocket = null;
    if (
      socket &&
      (socket.readyState === WebSocket.CONNECTING ||
        socket.readyState === WebSocket.OPEN)
    ) {
      socket.close();
    }
  }

  private closeNudgeSocket(): void {
    const socket = this.nudgeSocket;
    this.nudgeSocket = null;
    if (
      socket &&
      (socket.readyState === WebSocket.CONNECTING ||
        socket.readyState === WebSocket.OPEN)
    ) {
      socket.close();
    }
  }
}
