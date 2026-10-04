/**
 * PCM conversion for the capture path.
 *
 * Discord's Opus decoder output is 48 kHz, 2-channel, signed 16-bit little-endian.
 * Speech-to-text wants 16 kHz mono. 48 000 / 16 000 = 3, so each output sample is the
 * average of 3 input frames (6 samples: 3 x L+R). The 3-tap box filter doubles as a
 * cheap low-pass to limit aliasing; it is plenty for speech.
 */

const IN_CHANNELS = 2;
const DECIMATION = 3;
const BYTES_PER_SAMPLE = 2;
/** Bytes of 48 kHz stereo input consumed per output sample. */
const IN_BLOCK_BYTES = IN_CHANNELS * DECIMATION * BYTES_PER_SAMPLE; // 12

/**
 * Stateful 48 kHz stereo -> 16 kHz mono converter. Keeps leftover bytes between calls,
 * so it accepts chunks of any size without dropping or duplicating audio.
 */
export class Downsampler {
  private remainder: Buffer = Buffer.alloc(0);

  push(chunk: Buffer): Buffer {
    const input = this.remainder.length > 0 ? Buffer.concat([this.remainder, chunk]) : chunk;
    const blocks = Math.floor(input.length / IN_BLOCK_BYTES);
    const out = Buffer.allocUnsafe(blocks * BYTES_PER_SAMPLE);

    for (let b = 0; b < blocks; b++) {
      const base = b * IN_BLOCK_BYTES;
      let sum = 0;
      for (let i = 0; i < IN_CHANNELS * DECIMATION; i++) {
        sum += input.readInt16LE(base + i * BYTES_PER_SAMPLE);
      }
      out.writeInt16LE(Math.round(sum / (IN_CHANNELS * DECIMATION)), b * BYTES_PER_SAMPLE);
    }

    const used = blocks * IN_BLOCK_BYTES;
    this.remainder = Buffer.from(input.subarray(used));
    return out;
  }

  reset(): void {
    this.remainder = Buffer.alloc(0);
  }
}
