"""The child process that runs the speech-sound model (#2316). Started by
`said_as.recogniser.WorkerRecogniser`, never imported by the bot.

Protocol on stdin and stdout, so the bot never imports the machine-learning runtime: after the
weights load it writes one JSON line `{"ready": true}`; then for each request it reads eight bytes
(the request number, then the length) and that many bytes of 16 kHz mono s16le audio, and writes
one JSON line `{"n": <the request number>, "ipa": "<speech sounds>"}`. The model is a
phoneme-recognition model (a wav2vec2 fine-tuned to write IPA), loaded from the folder given as
the only argument. Its runtime (`torch`, `transformers`) is installed on the server by dev1
(#2318) and is not a dependency of DMbot's own code; the audio is never written to disk and
nothing is printed except the two kinds of JSON line.
"""

from __future__ import annotations

import importlib
import json
import os
import struct
import sys
from typing import Any

SAMPLE_RATE = 16000
MAX_IPA = 1000  # characters of speech sounds sent back: a name is a handful
MAX_BYTES = 10 * SAMPLE_RATE * 2 + 4096  # ten seconds, with a little room: more is refused


def load(model_dir: str) -> tuple[Any, Any, Any]:
    """The runtime, the processor and the model, loaded from `model_dir` only (no downloads)."""
    torch = importlib.import_module("torch")
    transformers = importlib.import_module("transformers")
    torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", "2")))  # the rest is the table's
    processor = transformers.AutoProcessor.from_pretrained(model_dir, local_files_only=True)
    model = transformers.AutoModelForCTC.from_pretrained(model_dir, local_files_only=True)
    model.eval()
    return torch, processor, model


def recognise(torch: Any, processor: Any, model: Any, pcm: bytes) -> str:
    """The speech sounds in `pcm`, joined into one string with no spaces."""
    numpy = importlib.import_module("numpy")
    audio = numpy.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
    inputs = processor(audio, sampling_rate=SAMPLE_RATE, return_tensors="pt")
    with torch.inference_mode():
        logits = model(inputs.input_values).logits
    ids = torch.argmax(logits, dim=-1)
    return "".join(str(processor.batch_decode(ids)[0]).split())


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 2
    try:
        torch, processor, model = load(argv[1])
    except Exception:
        return 3  # the bot sees the pipe close and says the voice is off
    out = sys.stdout
    sys.stdout = sys.stderr  # a library's print must not land in the protocol's channel
    out.write(json.dumps({"ready": True}) + "\n")
    out.flush()
    stdin = sys.stdin.buffer
    while True:
        head = stdin.read(8)
        if len(head) < 8:
            return 0  # the bot closed the pipe
        number, size = struct.unpack(">II", head)
        if size > MAX_BYTES:
            return 4
        pcm = stdin.read(size)
        if len(pcm) < size:
            return 0
        ipa = recognise(torch, processor, model, pcm)[:MAX_IPA]
        out.write(json.dumps({"n": number, "ipa": ipa}) + "\n")
        out.flush()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
