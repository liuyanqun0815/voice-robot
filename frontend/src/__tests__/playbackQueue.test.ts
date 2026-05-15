import { describe, expect, it } from "vitest";

import { PlaybackQueue } from "../audio/playbackQueue";

describe("PlaybackQueue", () => {
  it("dequeues in insertion order", () => {
    const queue = new PlaybackQueue();
    queue.enqueue("a");
    queue.enqueue("b");

    expect(queue.dequeue()).toBe("a");
    expect(queue.dequeue()).toBe("b");
  });
});
