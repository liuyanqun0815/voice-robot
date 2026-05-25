import React, { useEffect, useMemo, useRef, useState } from "react";

import { startAudioCaptureWithVAD, type CaptureController } from "../audio/audioCapture";
import { TypewriterText } from "../components/TypewriterText";
import { SessionStore, type SessionState } from "../store/sessionStore";
import { VoiceSocket } from "../ws/voiceSocket";
import type { InputMode } from "../ws/events";

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

const GREETING_TURN_ID = "__greeting__";

function appendAssistantStreamMessage(prev: ChatItem[], turnId: string, delta: string): ChatItem[] {
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
  /** 文本提交时写入；turn_committed 时用于渲染用户消息（无 ASR） */
  const pendingUserTextRef = useRef<string>("");
  const generationIdRef = useRef<string>("");
  /** 用户打断后忽略旧 generation 的 llm/tts 下行，直至下一轮 turn_committed */
  const streamSuppressedRef = useRef<boolean>(false);
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
  const [textDraft, setTextDraft] = useState("");
  const [inputMode, setInputMode] = useState<InputMode>("voice");
  const [state, setState] = useState<SessionState>("listening");
  const [messages, setMessages] = useState<ChatItem[]>([]);

  const busy = state === "thinking" || state === "speaking";

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
          wsRef.current?.sendSessionInit({
            type: "session_init",
            session_id: sessionIdRef.current,
            turn_id: turnIdRef.current,
            seq: nextSeq(),
            trace_id: "tr_voice_page",
            timestamp_ms: Date.now(),
          });
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

        if (eventType === "greeting_delta") {
          if (streamSuppressedRef.current) {
            return;
          }
          const delta = String(event.text ?? "");
          if (!delta) {
            return;
          }
          setMessages((prev) => {
            if (
              prev.some((item) => item.role === "assistant" && item.turnId === GREETING_TURN_ID && !item.isStreaming)
            ) {
              return prev;
            }
            return appendAssistantStreamMessage(prev, GREETING_TURN_ID, delta);
          });
          return;
        }

        if (eventType === "greeting_complete") {
          setMessages((prev) =>
            prev.map((item) =>
              item.turnId === GREETING_TURN_ID ? { ...item, isStreaming: false } : item,
            ),
          );
          return;
        }

        if (eventType === "session_greeting") {
          const text = String(event.text ?? "").trim();
          if (!text) {
            return;
          }
          setMessages((prev) => {
            if (prev.some((item) => item.role === "assistant" && item.turnId === GREETING_TURN_ID)) {
              return prev;
            }
            return [
              ...prev,
              { role: "assistant", text, turnId: GREETING_TURN_ID, isStreaming: true },
            ];
          });
          return;
        }

        if (eventType === "cancel_ack") {
          if (event.cancelled === true) {
            streamSuppressedRef.current = true;
            // 自动打断后紧接新提交时，勿清空 pending / 勿切 interrupted，否则左侧看不到新用户消息
            if (!commitInFlightRef.current) {
              beginNewTurn();
              storeRef.current.setState("interrupted");
              setState("interrupted");
            }
          }
          return;
        }

        if (eventType === "turn_committed") {
          console.info("[voice/page] turn committed");
          commitInFlightRef.current = false;
          streamSuppressedRef.current = false;
          generationIdRef.current = String(event.generation_id ?? "");
          storeRef.current.setState("thinking");
          setState("thinking");
          const userText = (pendingUserTextRef.current || asrFinalTextRef.current || asrTextRef.current).trim();
          if (userText) {
            appendUserMessage(userText);
          }
          pendingUserTextRef.current = "";
          setAsrText("");
          asrTextRef.current = "";
          asrFinalTextRef.current = "";
          return;
        }

        if (eventType === "turn_rejected") {
          console.info("[voice/page] turn rejected");
          beginNewTurn();
          const code = String(event.error_code ?? "");
          setMessages((prev) => [
            ...prev,
            { role: "system", text: `提交被拒绝（${code || "未知"}），请重新发送。` },
          ]);
          return;
        }

        if (eventType === "llm_delta" || eventType === "tts_chunk" || eventType === "audio_complete") {
          if (streamSuppressedRef.current) {
            return;
          }
          const eventGen = String(event.generation_id ?? "");
          if (
            eventGen &&
            generationIdRef.current &&
            eventGen !== generationIdRef.current
          ) {
            return;
          }
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
          setMessages((prev) => appendAssistantStreamMessage(prev, turnId, delta));
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
          const completeTurnId = String(event.turn_id ?? "");
          if (completeTurnId === GREETING_TURN_ID) {
            setMessages((prev) =>
              prev.map((item) =>
                item.turnId === GREETING_TURN_ID ? { ...item, isStreaming: false } : item,
              ),
            );
            storeRef.current.setState("listening");
            setState("listening");
            return;
          }
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

  /** 打断或拒单后开启新轮次，避免复用已 commit 的 turn_id */
  const beginNewTurn = (): void => {
    turnIdRef.current = randomId("turn");
    generationIdRef.current = "";
    lastCommittedTextRef.current = "";
    commitInFlightRef.current = false;
    pendingUserTextRef.current = "";
    assistantStreamTurnRef.current = "";
    storeRef.current.setState("listening");
    setState("listening");
  };

  /** 停止当前 generation（与「打断 AI」相同），可选标记 interrupted 状态 */
  const interruptActiveGeneration = (options?: { markInterrupted?: boolean }): void => {
    if (!wsRef.current) {
      return;
    }
    if (generationIdRef.current) {
      console.info("[voice/page] interrupt active generation");
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
    }
    streamSuppressedRef.current = true;
    setMessages((prev) => prev.map((item) => (item.isStreaming ? { ...item, isStreaming: false } : item)));
    beginNewTurn();
    if (options?.markInterrupted) {
      storeRef.current.setState("interrupted");
      setState("interrupted");
    }
  };

  const appendUserMessage = (text: string): void => {
    if (!text || text === lastRenderedUserTextRef.current) {
      return;
    }
    lastRenderedUserTextRef.current = text;
    setMessages((prev) => [...prev, { role: "user", text }]);
  };

  const commitTurn = (userText: string, mode: InputMode): void => {
    if (!wsRef.current || !connected) {
      return;
    }
    const trimmed = userText.trim();
    if (!trimmed) {
      return;
    }
    if (commitInFlightRef.current) {
      return;
    }
    if (state === "interrupted") {
      beginNewTurn();
    }
    if (trimmed === lastCommittedTextRef.current) {
      return;
    }
    commitInFlightRef.current = true;
    lastCommittedTextRef.current = trimmed;
    pendingUserTextRef.current = trimmed;
    if (mode === "text") {
      appendUserMessage(trimmed);
    }
    console.info("[voice/page] commit turn", { mode, turnId: turnIdRef.current });
    wsRef.current.sendCommit({
      type: "turn_commit_request",
      session_id: sessionIdRef.current,
      turn_id: turnIdRef.current,
      seq: nextSeq(),
      trace_id: "tr_voice_page",
      timestamp_ms: Date.now(),
      reason: trimmed,
      input_mode: mode,
    });
  };

  const switchInputMode = (mode: InputMode): void => {
    if (mode === inputMode) {
      return;
    }
    if (mode === "text" && recording) {
      stopVoice();
    }
    setInputMode(mode);
  };

  const submitText = (): void => {
    const text = textDraft.trim();
    if (!text || !connected) {
      return;
    }
    if (busy) {
      interruptActiveGeneration();
    }
    commitTurn(text, "text");
    setTextDraft("");
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
          commitTurn(userText, "voice");
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
    if (!busy) {
      return;
    }
    interruptActiveGeneration({ markInterrupted: true });
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
            连接状态：{connected ? "已连接" : "未连接"} | 会话状态：{state} | 输入：
            {inputMode === "voice" ? "语音" : "文本"}
            {inputMode === "voice" ? ` | VAD：${userSpeaking ? "说话中" : "静音"}` : ""}
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
            {messages.length === 0 && (
              <p style={{ color: "#999" }}>
                {inputMode === "voice"
                  ? "暂无消息，右侧选择「语音输入」并点击「开启语音」开始。"
                  : "暂无消息，右侧选择「文本输入」输入问题后点击「发送」。"}
              </p>
            )}
            {messages.map((item, index) => (
              <p
                key={`${item.role}-${item.turnId ?? index}-${index}`}
                style={{
                  margin: "8px 0",
                  lineHeight: 1.55,
                  color: item.role === "assistant" ? "#1a1a1a" : undefined,
                }}
              >
                <strong>
                  {item.role === "assistant" ? "助手" : item.role === "user" ? "用户" : item.role}:
                </strong>{" "}
                {item.role === "assistant" && item.isStreaming ? (
                  <TypewriterText text={item.text} active />
                ) : (
                  item.text
                )}
              </p>
            ))}
          </div>
          {inputMode === "voice" ? (
            <div style={{ marginTop: 10, padding: 8, background: "#fafafa", borderRadius: 6 }}>
              <strong>ASR 实时文本：</strong>
              <span>{asrText || "（等待语音输入）"}</span>
            </div>
          ) : (
            <div style={{ marginTop: 10, padding: 8, background: "#fafafa", borderRadius: 6, color: "#666" }}>
              文本模式下，输入内容在右侧编辑并发送。
            </div>
          )}
        </section>

        <aside
          style={{
            width: 280,
            border: "2px solid #3d9e62",
            borderRadius: 8,
            padding: 12,
            background: "#fff",
          }}
        >
          <h3>输入方式</h3>
          <div
            style={{
              display: "flex",
              gap: 4,
              marginBottom: 12,
              padding: 4,
              background: "#f0f0f0",
              borderRadius: 8,
            }}
          >
            <button
              type="button"
              onClick={() => switchInputMode("voice")}
              style={{
                flex: 1,
                padding: "8px 6px",
                border: "none",
                borderRadius: 6,
                cursor: "pointer",
                background: inputMode === "voice" ? "#fff" : "transparent",
                fontWeight: inputMode === "voice" ? 600 : 400,
                boxShadow: inputMode === "voice" ? "0 1px 3px rgba(0,0,0,0.12)" : "none",
              }}
            >
              语音输入
            </button>
            <button
              type="button"
              onClick={() => switchInputMode("text")}
              style={{
                flex: 1,
                padding: "8px 6px",
                border: "none",
                borderRadius: 6,
                cursor: "pointer",
                background: inputMode === "text" ? "#fff" : "transparent",
                fontWeight: inputMode === "text" ? 600 : 400,
                boxShadow: inputMode === "text" ? "0 1px 3px rgba(0,0,0,0.12)" : "none",
              }}
            >
              文本输入
            </button>
          </div>

          {inputMode === "voice" ? (
            <>
              <button
                onClick={toggleVoice}
                disabled={!connected || busy}
                style={{ width: "100%", marginBottom: 8, padding: 10 }}
              >
                {recording ? "关闭语音" : "开启语音"}
              </button>
              <p style={{ fontSize: 12, color: "#666", marginBottom: 12 }}>
                开启后自动采集；VAD 检测到说话结束会自动提交。
              </p>
            </>
          ) : (
            <>
              <textarea
                value={textDraft}
                onChange={(e) => setTextDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    submitText();
                  }
                }}
                placeholder="输入问题，Enter 发送，Shift+Enter 换行"
                disabled={!connected}
                rows={5}
                style={{
                  width: "100%",
                  boxSizing: "border-box",
                  marginBottom: 8,
                  padding: 10,
                  borderRadius: 6,
                  border: "1px solid #ccc",
                  resize: "vertical",
                  fontFamily: "inherit",
                  fontSize: 14,
                }}
              />
              <button
                onClick={submitText}
                disabled={!connected || !textDraft.trim()}
                style={{ width: "100%", marginBottom: 8, padding: 10 }}
              >
                发送
              </button>
              <p style={{ fontSize: 12, color: "#666", marginBottom: 12 }}>
                文本将直接提交给助手，不经过语音识别。AI 回复中再次发送将自动打断并用新问题继续。
              </p>
            </>
          )}

          <button onClick={cancelSpeaking} disabled={!connected || !busy} style={{ width: "100%", padding: 10 }}>
            打断 AI
          </button>
        </aside>
      </div>
    </main>
  );
}
