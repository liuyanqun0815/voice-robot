export type VadEventType = "speech_start" | "speech_end";

export class VadController {
  constructor(private readonly onVadEvent: (event: VadEventType) => void) {}

  emitSpeechStart(): void {
    this.onVadEvent("speech_start");
  }

  emitSpeechEnd(): void {
    this.onVadEvent("speech_end");
  }
}
