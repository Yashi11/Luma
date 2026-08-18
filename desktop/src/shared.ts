export type Rectangle = { x: number; y: number; width: number; height: number };
export type SelectionResult = { displayId: string; rectangle: Rectangle };
export type PreviewPayload = { captureId: string; imageDataUrl: string };
export type Explanation = { explanation: string; uncertainty: string | null; needsMoreContext: boolean };
