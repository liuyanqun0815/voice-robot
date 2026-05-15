import React, { useEffect, useState } from "react";

type TypewriterTextProps = {
  /** 流式累积的完整目标文本 */
  text: string;
  /** 每个字符的显示间隔（毫秒） */
  speedMs?: number;
  /** 是否仍在流式接收（显示光标） */
  active?: boolean;
};

/**
 * 将目标文本以打字机方式逐字展示；text 增长时自动追赶显示进度。
 */
export function TypewriterText({ text, speedMs = 28, active = true }: TypewriterTextProps): JSX.Element {
  const [visibleLen, setVisibleLen] = useState(0);

  useEffect(() => {
    if (text.length < visibleLen) {
      setVisibleLen(0);
    }
  }, [text, visibleLen]);

  useEffect(() => {
    if (!active && visibleLen < text.length) {
      setVisibleLen(text.length);
      return;
    }
    if (visibleLen >= text.length) {
      return;
    }
    const timer = window.setTimeout(() => {
      setVisibleLen((prev) => Math.min(prev + 1, text.length));
    }, speedMs);
    return () => window.clearTimeout(timer);
  }, [text, visibleLen, speedMs, active]);

  const displayed = text.slice(0, visibleLen);
  const showCursor = active && (visibleLen < text.length || text.length === 0);

  return (
    <span>
      {displayed}
      {showCursor ? (
        <span
          aria-hidden
          style={{
            display: "inline-block",
            width: 2,
            height: "1em",
            marginLeft: 2,
            verticalAlign: "text-bottom",
            background: "#1677ff",
            animation: "voice-typewriter-blink 0.9s step-end infinite",
          }}
        />
      ) : null}
      <style>{`
        @keyframes voice-typewriter-blink {
          50% { opacity: 0; }
        }
      `}</style>
    </span>
  );
}
