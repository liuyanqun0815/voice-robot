export class PlaybackQueue {
  private readonly queue: string[] = [];

  enqueue(chunk: string): void {
    this.queue.push(chunk);
  }

  dequeue(): string | undefined {
    return this.queue.shift();
  }

  clear(): void {
    this.queue.length = 0;
  }

  size(): number {
    return this.queue.length;
  }
}
