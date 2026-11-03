/**
 * The microphone for Rika's voice input (docs/rika-design.md Q5 A). The page captures
 * with getUserMedia and hands the backend 16 kHz mono little-endian 16-bit PCM over
 * Rika's WebSocket; transcription runs on this computer (kitty.core.rika.voice).
 *
 * Capture uses a ScriptProcessorNode rather than an AudioWorklet: the desktop webview
 * (WebKitGTK) and Chromium both run it, and it needs no worklet module, which the app's
 * `script-src 'self'` policy would otherwise have to serve as a separate file.
 */
export const VOICE_RATE = 16_000;
/** ~85 ms per frame at 48 kHz: small enough that the transcript keeps up. */
const BUFFER = 4096;

/** Converts float samples at any rate to 16 kHz Int16, carrying leftover samples across frames. */
export class Downsampler {
  private position = 0;
  private carry = new Float32Array(0);
  private readonly step: number;

  constructor(from: number) {
    this.step = from / VOICE_RATE;
  }

  push(input: Float32Array): Int16Array {
    let samples = input;
    if (this.carry.length) {
      samples = new Float32Array(this.carry.length + input.length);
      samples.set(this.carry);
      samples.set(input, this.carry.length);
    }
    const out: number[] = [];
    let position = this.position;
    // Average the input samples each output sample spans (a box low-pass against aliasing).
    while (position + this.step <= samples.length) {
      const start = Math.floor(position);
      const end = Math.max(start + 1, Math.floor(position + this.step));
      let sum = 0;
      for (let index = start; index < end; index += 1) sum += samples[index];
      out.push(sum / (end - start));
      position += this.step;
    }
    const kept = Math.floor(position);
    this.carry = samples.slice(kept);
    this.position = position - kept;
    const pcm = new Int16Array(out.length);
    for (let index = 0; index < out.length; index += 1) {
      const value = Math.max(-1, Math.min(1, out[index]));
      pcm[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
    }
    return pcm;
  }
}

export class MicError extends Error {}

/** Little-endian bytes of the samples, whatever the platform's byte order. */
export function littleEndian(pcm: Int16Array): ArrayBuffer {
  const view = new DataView(new ArrayBuffer(pcm.length * 2));
  for (let index = 0; index < pcm.length; index += 1) view.setInt16(index * 2, pcm[index], true);
  return view.buffer;
}

/** One open microphone; `frame` receives each chunk of 16 kHz PCM bytes. */
export class Mic {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private stopped = false;

  async start(frame: (pcm: ArrayBuffer) => void) {
    if (!navigator.mediaDevices?.getUserMedia) throw new MicError("This window cannot use a microphone");
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (cause) {
      const name = (cause as DOMException)?.name;
      throw new MicError(
        name === "NotAllowedError" || name === "SecurityError" ? "Microphone access was denied; allow it to talk to Rika"
          : name === "NotFoundError" ? "No microphone was found" : "The microphone could not be opened",
      );
    }
    if (this.stopped) return this.stop();
    const context = (this.context = new AudioContext());
    await context.resume().catch(() => {});
    const source = context.createMediaStreamSource(this.stream);
    const processor = context.createScriptProcessor(BUFFER, 1, 1);
    const sink = context.createGain();
    sink.gain.value = 0; // the processor only runs while connected to the output; nothing is heard
    const resample = new Downsampler(context.sampleRate);
    processor.onaudioprocess = (event) => {
      if (this.stopped) return;
      const pcm = resample.push(event.inputBuffer.getChannelData(0));
      if (pcm.length) frame(littleEndian(pcm));
    };
    source.connect(processor);
    processor.connect(sink);
    sink.connect(context.destination);
  }

  stop() {
    this.stopped = true;
    this.stream?.getTracks().forEach((track) => track.stop());
    void this.context?.close().catch(() => {});
    this.stream = null;
    this.context = null;
  }
}
