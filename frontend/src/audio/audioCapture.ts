export type AudioFrameHandler = (base64Pcm: string) => void;
export type VADHandler = () => void;

export type CaptureController = {
  stop: () => void;
};

export async function startAudioCapture(onFrame: AudioFrameHandler): Promise<CaptureController> {
  return startAudioCaptureWithVAD(onFrame);
}

function pcm16ToBase64(pcm16: Int16Array): string {
  const bytes = new Uint8Array(pcm16.buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index]);
  }
  return btoa(binary);
}

function downsampleTo16k(input: Float32Array, inputSampleRate: number): Int16Array {
  if (inputSampleRate === 16000) {
    const direct = new Int16Array(input.length);
    for (let index = 0; index < input.length; index += 1) {
      const sample = Math.max(-1, Math.min(1, input[index]));
      direct[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return direct;
  }

  const ratio = inputSampleRate / 16000;
  const outputLength = Math.floor(input.length / ratio);
  const output = new Int16Array(outputLength);
  let outputOffset = 0;
  let inputOffset = 0;

  while (outputOffset < outputLength) {
    const nextInputOffset = Math.floor((outputOffset + 1) * ratio);
    let accum = 0;
    let count = 0;
    for (let idx = inputOffset; idx < nextInputOffset && idx < input.length; idx += 1) {
      accum += input[idx];
      count += 1;
    }
    const sample = Math.max(-1, Math.min(1, accum / Math.max(1, count)));
    output[outputOffset] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    outputOffset += 1;
    inputOffset = nextInputOffset;
  }
  return output;
}

export async function startAudioCaptureWithVAD(
  onFrame: AudioFrameHandler,
  options?: {
    onSpeechStart?: VADHandler;
    onSpeechEnd?: VADHandler;
    vadThreshold?: number;
    vadSilenceMs?: number;
    vadCheckIntervalMs?: number;
  },
): Promise<CaptureController> {
  console.info("[voice/capture] requesting microphone permission");
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      sampleRate: 16000,
    },
  });

  const audioContext = new AudioContext();
  // 采集链路：
  // 麦克风(Float32) -> 16k重采样 -> Int16 PCM -> base64 分片上行
  const source = audioContext.createMediaStreamSource(stream);
  const analyser = audioContext.createAnalyser();
  analyser.fftSize = 2048;
  const processor = audioContext.createScriptProcessor(4096, 1, 1);
  source.connect(analyser);
  source.connect(processor);
  processor.connect(audioContext.destination);

  const pcmCache: number[] = [];
  const targetSamplesPerChunk = 3200; // 200ms at 16k
  processor.onaudioprocess = (event) => {
    const inputData = event.inputBuffer.getChannelData(0);
    const downsampled = downsampleTo16k(inputData, audioContext.sampleRate);
    for (let index = 0; index < downsampled.length; index += 1) {
      pcmCache.push(downsampled[index]);
    }
    while (pcmCache.length >= targetSamplesPerChunk) {
      const chunk = pcmCache.splice(0, targetSamplesPerChunk);
      const chunkArray = Int16Array.from(chunk);
      // 每 200ms 推送一次，平衡实时性和网络开销。
      onFrame(pcm16ToBase64(chunkArray));
    }
  };

  const buffer = new Float32Array(analyser.fftSize);

  let speaking = false;
  let silenceMs = 0;
  const vadThreshold = options?.vadThreshold ?? 0.018;
  const vadSilenceMs = options?.vadSilenceMs ?? 650;
  const checkIntervalMs = options?.vadCheckIntervalMs ?? 80;
  // VAD 策略：
  // - RMS 超阈值 => speech_start
  // - 连续静音超阈值 => speech_end
  const vadTimer = window.setInterval(() => {
    analyser.getFloatTimeDomainData(buffer);
    let sumSquares = 0;
    for (let index = 0; index < buffer.length; index += 1) {
      sumSquares += buffer[index] * buffer[index];
    }
    const rms = Math.sqrt(sumSquares / buffer.length);

    if (rms >= vadThreshold) {
      silenceMs = 0;
      if (!speaking) {
        speaking = true;
        console.debug("[voice/vad] speech_start");
        options?.onSpeechStart?.();
      }
      return;
    }

    if (!speaking) {
      return;
    }
    silenceMs += checkIntervalMs;
    if (silenceMs >= vadSilenceMs) {
      speaking = false;
      silenceMs = 0;
      console.debug("[voice/vad] speech_end");
      options?.onSpeechEnd?.();
    }
  }, checkIntervalMs);

  return {
    stop: () => {
      console.info("[voice/capture] stop capture");
      window.clearInterval(vadTimer);
      processor.disconnect();
      source.disconnect();
      analyser.disconnect();
      void audioContext.close();
      stream.getTracks().forEach((track) => track.stop());
    },
  };
}
