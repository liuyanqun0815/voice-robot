export type SessionState = "listening" | "thinking" | "speaking" | "interrupted" | "completed";

export class SessionStore {
  private state: SessionState = "listening";

  setState(next: SessionState): void {
    this.state = next;
  }

  getState(): SessionState {
    return this.state;
  }
}
