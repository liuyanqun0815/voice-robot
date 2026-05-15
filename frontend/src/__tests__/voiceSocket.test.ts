import { describe, expect, it } from "vitest";

import type { TurnCommitRequestEvent } from "../ws/events";

describe("events type", () => {
  it("builds turn commit payload", () => {
    const event: TurnCommitRequestEvent = {
      type: "turn_commit_request",
      session_id: "s1",
      turn_id: "t1",
      reason: "frontend_vad_end",
      timestamp_ms: Date.now()
    };

    expect(event.type).toBe("turn_commit_request");
    expect(event.reason).toBe("frontend_vad_end");
  });
});
