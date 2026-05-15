import React, { useEffect, useMemo, useRef, useState } from "react";

import { startAudioCaptureWithVAD, type CaptureController } from "../audio/audioCapture";
import { TypewriterText } from "../components/TypewriterText";
import { SessionStore, type SessionState } from "../store/sessionStore";
import { VoiceSocket } from "../ws/voiceSocket";

type ChatItem = {
  role: "user" | "assistant" | "system";
  text: string;
  /** 助手消息是否处于 llm_delta 流式输出中 */
  isStreaming?: boolean;
  turnId?: string;
};

function randomId(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

export function VoicePage(): JSX.Element {
  const wsRef = useRef<VoiceSocket | null>(null);
  const captureRef = useRef<CaptureController | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const recordingRef = useRef<boolean>(false);
  const userSpeakingRef = useRef<boolean>(false);
  const commitInFlightRef = useRef<boolean>(false);
  const asrTextRef = useRef<string>("");
  const asrFinalTextRef = useRef<string>("");
  const lastCommittedTextRef = useRef<string>("");
  const lastRenderedUserTextRef = useRef<string>("");
  const generationIdRef = useRef<string>("");
  /** 当前轮次助手流式文本合并目标（与 event.turn_id 对齐） */
  const assistantStreamTurnRef = useRef<string>("");
  const turnIdRef = useRef<string>(randomId("turn"));
  const sessionIdRef = useRef<string>(randomId("session"));
  const seqRef = useRef<number>(1);
  const storeRef = useRef<SessionStore>(new SessionStore());
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const [connected, setConnected] = useState(false);
  const [recording, setRecording] = useState(false);
  const [userSpeaking, setUserSpeaking] = useState(false);
  const [asrText, setAsrText] = useState("");
  const [state, setState] = useState<SessionState>("listening");
  const [messages, setMessages] = useState<ChatItem[]>([]);

  const wsUrl = useMemo(() => "ws://127.0.0.1:8000/ws/voice", []);

  useEffect(() => {
    chatScrollRef.current?.scrollTo({ top: chatScrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, asrText]);

  useEffect(() => {
    let disposed = false;

    const connectSocket = (): void => {
      if (disposed) {
        return;
      }
      console.info("[voice/page] init websocket");
      const socket = new VoiceSocket(wsUrl);
      wsRef.current = socket;

      socket
        .waitUntilOpen()
        .then(() => {
          if (disposed) {
            return;
          }
          console.info("[voice/page] websocket ready");
          setConnected(true);
        })
        .catch(() => {
          if (disposed) {
            return;
          }
          setConnected(false);
          console.warn("[voice/page] websocket open failed, retrying");
          setMessages((prev) => [...prev, { role: "system", text: "WebSocket 连接失败，正在重试..." }]);
          reconnectTimerRef.current = window.setTimeout(connectSocket, 1500);
        });

      socket.onClose(() => {
        if (disposed) {
          return;
        }
        setConnected(false);
        console.warn("[voice/page] websocket closed, retrying");
        reconnectTimerRef.current = window.setTimeout(connectSocket, 1500);
      });

      socket.onError(() => {
        if (disposed) {
          return;
        }
        setConnected(false);
        console.warn("[voice/page] websocket error");
      });

      socket.onMessage((raw) => {
        // 统一事件分发：
        // - asr_partial/asr_final：字幕态
        // - turn_committed：进入thinking并固化user消息
        // - llm_delta/tts_chunk/audio_complete：助手流式回答与播报
        const event = JSON.parse(raw.data) as Record<string, string | boolean>;
        const eventType = String(event.type ?? "");

        if (eventType === "asr_partial") {
          const text = String(event.text ?? "");
          asrTextRef.current = text;
          setAsrText(text);
          return;
        }

        if (eventType === "asr_final") {
          const text = String(event.text ?? "");
          asrTextRef.current = text;
          asrFinalTextRef.current = text;
          setAsrText(text);
          return;
        }

        if (eventType === "turn_committed") {
          console.info("[voice/page] turn committed");
          commitInFlightRef.current = false;
          generationIdRef.current = String(event.generation_id ?? "");
          storeRef.current.setState("thinking");
          setState("thinking");
          const userText = (asrFinalTextRef.current || asrTextRef.current).trim();
          if (userText && userText !== lastRenderedUserTextRef.current) {
            // 只渲染一次用户最终文本，避免重复“user:”行。
            lastRenderedUserTextRef.current = userText;
            setMessages((prev) => [...prev, { role: "user", text: userText }]);
            setAsrText("");
            asrTextRef.current = "";
            asrFinalTextRef.current = "";
          }
          return;
        }

        if (eventType === "turn_rejected") {
          console.info("[voice/page] turn rejected");
          commitInFlightRef.current = false;
          return;
        }

        if (eventType === "llm_delta") {
          const turnId = String(event.turn_id ?? "");
          const delta = String(event.text ?? "");
          if (!delta) {
            return;
          }
          storeRef.current.setState("speaking");
          setState("speaking");
          assistantStreamTurnRef.current = turnId;
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.role === "assistant" && last.isStreaming && last.turnId === turnId) {
              return [
                ...prev.slice(0, -1),
                {
                  role: "assistant",
                  text: last.text + delta,
                  isStreaming: true,
                  turnId,
                },
              ];
            }
            return [
              ...prev,
              {
                role: "assistant",
                text: delta,
                isStreaming: true,
                turnId,
              },
            ];
          });
          return;
        }

        if (eventType === "llm_chunk") {
          storeRef.current.setState("speaking");
          setState("speaking");
          const text = String(event.text ?? "");
          setMessages((prev) => [...prev, { role: "assistant", text }]);
          return;
        }

        if (eventType === "audio_complete") {
          console.info("[voice/page] audio complete");
          storeRef.current.setState("listening");
          setState("listening");
          setMessages((prev) =>
            prev.map((item) => (item.isStreaming ? { ...item, isStreaming: false } : item)),
          );
          assistantStreamTurnRef.current = "";
          generationIdRef.current = "";
          commitInFlightRef.current = false;
          turnIdRef.current = randomId("turn");
          return;
        }

        if (eventType === "tts_error") {
          return;
        }

        if (eventType === "error") {
          const text = String(event.message ?? "未知错误");
          console.error("[voice/page] server error:", text);
          setMessages((prev) => [...prev, { role: "system", text: `服务端错误: ${text}` }]);
        }
      });
    };

    connectSocket();

    return () => {
      disposed = true;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
      }
      captureRef.current?.stop();
      wsRef.current?.close();
    };
  }, [wsUrl]);

  const nextSeq = (): number => {
    const current = seqRef.current;
    seqRef.current += 1;
    return current;
  };

  const startVoice = async (): Promise<void> => {
    if (!wsRef.current) {
      return;
    }
    console.info("[voice/page] start voice capture");
    const controller = await startAudioCaptureWithVAD(
      (base64Frame) => {
        if (!wsRef.current || !recordingRef.current) {
          return;
        }
        if (!userSpeakingRef.current) {
          // 只在“检测到说话”窗口发送音频，避免静音噪声污染识别。
          return;
        }
        wsRef.current.sendAudio({
          type: "audio_chunk",
          session_id: sessionIdRef.current,
          turn_id: turnIdRef.current,
          seq: nextSeq(),
          trace_id: "tr_voice_page",
          timestamp_ms: Date.now(),
          audio_base64: base64Frame,
          codec: "pcm_s16le",
          sample_rate_hz: 16000,
          channels: 1,
          chunk_ms: 200,
        });
      },
      {
        onSpeechStart: () => {
          userSpeakingRef.current = true;
          commitInFlightRef.current = false;
          console.debug("[voice/page] vad speech_start");
          setUserSpeaking(true);
          wsRef.current?.sendVadEvent({
            type: "vad_event",
            session_id: sessionIdRef.current,
            turn_id: turnIdRef.current,
            seq: nextSeq(),
            trace_id: "tr_voice_page",
            timestamp_ms: Date.now(),
            event: "speech_start",
          });
        },
        onSpeechEnd: () => {
          userSpeakingRef.current = false;
          console.debug("[voice/page] vad speech_end");
          setUserSpeaking(false);
          wsRef.current?.sendVadEvent({
            type: "vad_event",
            session_id: sessionIdRef.current,
            turn_id: turnIdRef.current,
            seq: nextSeq(),
            trace_id: "tr_voice_page",
            timestamp_ms: Date.now(),
            event: "speech_end",
          });
          if (!wsRef.current || !recordingRef.current) {
            return;
          }
          if (commitInFlightRef.current) {
            // 防重：同一轮提交未返回前，不再次提交。
            return;
          }
          const userText = (asrFinalTextRef.current || asrTextRef.current).trim();
          if (!userText) {
            return;
          }
          if (userText === lastCommittedTextRef.current) {
            // 防抖：短时间重复同文本不重复提交。
            return;
          }
          commitInFlightRef.current = true;
          lastCommittedTextRef.current = userText;
          console.info("[voice/page] auto commit by vad");
          wsRef.current.sendCommit({
            type: "turn_commit_request",
            session_id: sessionIdRef.current,
            turn_id: turnIdRef.current,
            seq: nextSeq(),
            trace_id: "tr_voice_page",
            timestamp_ms: Date.now(),
            reason: userText,
          });
        },
      },
    );
    captureRef.current = controller;
    recordingRef.current = true;
    setRecording(true);
    storeRef.current.setState("listening");
    setState("listening");
  };

  const stopVoice = (): void => {
    // 关闭语音仅停止采集，不主动提交，提交由 VAD 自动触发。
    console.info("[voice/page] stop voice capture");
    captureRef.current?.stop();
    captureRef.current = null;
    recordingRef.current = false;
    userSpeakingRef.current = false;
    commitInFlightRef.current = false;
    setRecording(false);
    setUserSpeaking(false);
  };

  const toggleVoice = (): void => {
    if (recording) {
      stopVoice();
      return;
    }
    void startVoice();
  };

  const cancelSpeaking = (): void => {
    if (!wsRef.current || !generationIdRef.current) {
      return;
    }
    console.info("[voice/page] send cancel");
    wsRef.current.sendCancel({
      type: "cancel",
      session_id: sessionIdRef.current,
      turn_id: turnIdRef.current,
      seq: nextSeq(),
      trace_id: "tr_voice_page",
      timestamp_ms: Date.now(),
      generation_id: generationIdRef.current,
      reason: "barge_in",
    });
    storeRef.current.setState("interrupted");
    commitInFlightRef.current = false;
    setState("interrupted");
    setMessages((prev) => prev.map((item) => (item.isStreaming ? { ...item, isStreaming: false } : item)));
    assistantStreamTurnRef.current = "";
  };

  return (
    <main style={{ padding: 16, fontFamily: "Arial, sans-serif" }}>
      <h1>Voice Robot</h1>
      <div style={{ display: "flex", gap: 16 }}>
        <section
          style={{
            flex: 1,
            border: "1px solid #ddd",
            borderRadius: 8,
            padding: 12,
            minHeight: 380,
            background: "#fff",
          }}
        >
          <h3>对话区</h3>
          <p style={{ color: "#666" }}>
            连接状态：{connected ? "已连接" : "未连接"} | 会话状态：{state} | VAD：{userSpeaking ? "说话中" : "静音"}
          </p>
          <div
            ref={chatScrollRef}
            style={{
              border: "1px solid #eee",
              borderRadius: 6,
              minHeight: 240,
              maxHeight: 360,
              overflowY: "auto",
              padding: 8,
            }}
          >
            {messages.length === 0 && <p style={{ color: "#999" }}>暂无消息，点击右侧“开启语音”开始。</p>}
            {messages.map((item, index) => (
              <p
                key={`${item.role}-${item.turnId ?? index}-${index}`}
                style={{
                  margin: "8px 0",
                  lineHeight: 1.55,
                  color: item.role === "assistant" ? "#1a1a1a" : undefined,
                }}
              >
                <strong>{item.role === "assistant" ? "助手" : item.role}:</strong>{" "}
                {item.role === "assistant" && item.isStreaming ? (
                  <TypewriterText text={item.text} active />
                ) : (
                  item.text
                )}
              </p>
            ))}
          </div>
          <div style={{ marginTop: 10, padding: 8, background: "#fafafa", borderRadius: 6 }}>
            <strong>ASR 实时文本：</strong>
            <span>{asrText || "（等待语音输入）"}</span>
          </div>
        </section>

        <aside
          style={{
            width: 260,
            border: "1px solid #ddd",
            borderRadius: 8,
            padding: 12,
            background: "#fff",
          }}
        >
          <h3>语音控制</h3>
          <button onClick={toggleVoice} disabled={!connected} style={{ width: "100%", marginBottom: 8, padding: 10 }}>
            {recording ? "关闭语音" : "开启语音"}
          </button>
          <button onClick={cancelSpeaking} disabled={!connected} style={{ width: "100%", padding: 10 }}>
            打断 AI
          </button>
          <p style={{ fontSize: 12, color: "#666", marginTop: 12 }}>
            说明：开启语音后自动采集。VAD 检测到说话结束会自动提交，不再需要手动“停止并提交”。
          </p>
        </aside>
      </div>
    </main>
  );
}
