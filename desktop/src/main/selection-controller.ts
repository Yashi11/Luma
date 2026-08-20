import { randomBytes } from 'crypto';
import axios, { AxiosInstance } from 'axios';
import { BrowserWindow, Display, ipcMain, screen } from 'electron';
import log from 'electron-log';
import { serviceManager } from './services/manager';

/* eslint-disable no-await-in-loop -- startup readiness polling is intentionally sequential */

type Selection = {
  type: 'rectangle';
  x: number;
  y: number;
  width: number;
  height: number;
};

type PreviewState = {
  imageDataUrl?: string;
  status: 'preview' | 'sending' | 'answer' | 'error';
  answer?: string;
  uncertainty?: string | null;
  needsMoreContext?: boolean;
  error?: string;
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

export default class SelectionController {
  private readonly api: AxiosInstance;

  private overlayWindow: BrowserWindow | null = null;

  private previewWindow: BrowserWindow | null = null;

  private previewState: PreviewState = { status: 'preview' };

  private sessionId: string | null = null;

  private activeDisplayId: number | null = null;

  constructor(
    private readonly port: number,
    private readonly token: string,
    private readonly preloadPath: () => string,
    private readonly resolveHtmlPath: (htmlFileName: string) => string,
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
        OPENAI_MODEL: process.env.OPENAI_MODEL?.trim() || 'gpt-5.6',
        ...(process.env.OPENAI_API_KEY?.trim()
          ? { OPENAI_API_KEY: process.env.OPENAI_API_KEY.trim() }
          : {}),
      },
      true,
    );
    serviceManager.startService('visual-copilot-server');
  }

  async activate(): Promise<void> {
    this.closeWindows();
    const cursor = screen.getCursorScreenPoint();
    const display = screen.getDisplayNearestPoint(cursor);
    this.activeDisplayId = display.id;
    try {
      await this.waitUntilReady();
      const response = await this.api.post('/activate', {
        display: displayPayload(display),
      });
      this.sessionId = response.data.session_id;
      this.createOverlay(display);
    } catch (error) {
      this.showError(SelectionController.messageFor(error));
    }
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
    });
    this.overlayWindow
      .loadURL(`${this.resolveHtmlPath('index.html')}?view=selection-overlay`)
      .catch((error) =>
        log.error('[Visual Copilot] overlay load failed', error),
      );
    this.overlayWindow.once('ready-to-show', () => {
      this.overlayWindow?.show();
      this.overlayWindow?.focus();
    });
    this.overlayWindow.on('closed', () => {
      this.overlayWindow = null;
    });
  }

  private createPreview(display: Display): void {
    const width = 430;
    const height = Math.min(650, display.workArea.height - 32);
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
      webPreferences: { preload: this.preloadPath() },
    });
    this.previewWindow
      .loadURL(`${this.resolveHtmlPath('index.html')}?view=selection-preview`)
      .catch((error) =>
        log.error('[Visual Copilot] preview load failed', error),
      );
    this.previewWindow.once('ready-to-show', () => this.previewWindow?.show());
    this.previewWindow.on('closed', () => {
      this.previewWindow = null;
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
    ipcMain.removeAllListeners('selection-preview-ready');
    ipcMain.on('selection-preview-ready', (event) => {
      if (event.sender === this.previewWindow?.webContents)
        this.publishPreview();
    });
    ipcMain.removeAllListeners('selection-preview-submit');
    ipcMain.on('selection-preview-submit', (event, question: unknown) => {
      if (event.sender !== this.previewWindow?.webContents) return;
      this.submit(typeof question === 'string' ? question : '').catch((error) =>
        this.showError(SelectionController.messageFor(error)),
      );
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
      await this.api.post(`/sessions/${sessionId}/freeze`, { selection });
      this.overlayWindow?.hide();
      await new Promise((resolve) => {
        setTimeout(resolve, 60);
      });
      await this.api.post(`/sessions/${sessionId}/overlay-hidden`);
      const display = screen
        .getAllDisplays()
        .find((item) => item.id === this.activeDisplayId);
      if (!display)
        throw new Error('Display configuration changed; select again.');
      const capture = await this.api.post(`/sessions/${sessionId}/capture`, {
        display: displayPayload(display),
      });
      this.previewState = {
        status: 'preview',
        imageDataUrl: capture.data.image_data_url,
      };
      this.overlayWindow?.destroy();
      this.createPreview(display);
    } catch (error) {
      this.overlayWindow?.destroy();
      this.showError(SelectionController.messageFor(error));
    }
  }

  private async submit(question: string): Promise<void> {
    if (!this.sessionId || this.previewState.status === 'sending') return;
    const { sessionId } = this;
    this.previewState = {
      ...this.previewState,
      status: 'sending',
      error: undefined,
    };
    this.publishPreview();
    try {
      await this.api.post(`/sessions/${sessionId}/preview`, {
        question: question.trim() || null,
      });
      const response = await this.api.post(`/sessions/${sessionId}/send`);
      this.previewState = {
        ...this.previewState,
        status: 'answer',
        answer: response.data.explanation,
        uncertainty: response.data.uncertainty,
        needsMoreContext: response.data.needs_more_context,
      };
      this.publishPreview();
    } catch (error) {
      this.previewState = {
        ...this.previewState,
        status: 'error',
        error: SelectionController.messageFor(error),
      };
      this.publishPreview();
    }
  }

  private showError(message: string): void {
    log.error(`[Visual Copilot] ${message}`);
    const display = screen.getDisplayNearestPoint(
      screen.getCursorScreenPoint(),
    );
    this.previewState = { status: 'error', error: message };
    if (!this.previewWindow) this.createPreview(display);
    else this.publishPreview();
  }

  private static messageFor(error: unknown): string {
    if (axios.isAxiosError(error)) {
      const detail = error.response?.data?.detail;
      if (typeof detail === 'string') return detail;
      if (error.code === 'ECONNREFUSED')
        return 'Selection service is unavailable.';
    }
    return error instanceof Error
      ? error.message
      : 'Visual Copilot request failed.';
  }

  private async cancel(closePreview = true): Promise<void> {
    const { sessionId } = this;
    this.sessionId = null;
    if (sessionId) {
      await this.api
        .post(`/sessions/${sessionId}/cancel`)
        .catch(() => undefined);
    }
    this.overlayWindow?.destroy();
    if (closePreview) this.previewWindow?.destroy();
    else {
      this.previewWindow?.destroy();
      this.previewWindow = null;
    }
  }

  private closeWindows(): void {
    this.overlayWindow?.destroy();
    this.previewWindow?.destroy();
    this.overlayWindow = null;
    this.previewWindow = null;
    this.sessionId = null;
  }
}
