export default class PcmStreamPlayer {
  private context: AudioContext | null = null;

  private nextStartTime = 0;

  private sources = new Set<AudioBufferSourceNode>();

  prepare(): void {
    if (!this.context) this.context = new AudioContext();
    this.context.resume().catch(() => {});
  }

  enqueue(base64Pcm: string, sampleRate: number): void {
    this.prepare();
    if (!this.context) return;
    const binary = atob(base64Pcm);
    if (!binary.length || binary.length % 2) return;
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    const view = new DataView(bytes.buffer);
    const samples = new Float32Array(bytes.length / 2);
    for (let index = 0; index < samples.length; index += 1) {
      samples[index] = view.getInt16(index * 2, true) / 0x8000;
    }
    const buffer = this.context.createBuffer(1, samples.length, sampleRate);
    buffer.copyToChannel(samples, 0);
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    const startAt = Math.max(
      this.context.currentTime + 0.025,
      this.nextStartTime,
    );
    this.nextStartTime = startAt + buffer.duration;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
    source.start(startAt);
  }

  async whenIdle(): Promise<void> {
    if (!this.context) return;
    const remainingMs = Math.max(
      0,
      (this.nextStartTime - this.context.currentTime) * 1_000,
    );
    if (remainingMs > 0) {
      await new Promise<void>((resolve) => {
        window.setTimeout(resolve, remainingMs + 50);
      });
    }
  }

  stop(): void {
    this.sources.forEach((source) => {
      try {
        source.stop();
      } catch {
        // A source that already ended is harmless.
      }
    });
    this.sources.clear();
    this.nextStartTime = 0;
  }

  dispose(): void {
    this.stop();
    this.context?.close().catch(() => {});
    this.context = null;
  }
}
