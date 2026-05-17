#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
import wave
from array import array
from pathlib import Path

import sherpa_onnx


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SHERPA_MODEL_DIR = ROOT_DIR / "models" / "sherpa-onnx-zipformer-ru-2024-09-18"
DEFAULT_LMSTUDIO_BASE_URL = "http://localhost:1234/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a WAV file with Sherpa-ONNX and forward it to LM Studio.",
    )
    parser.add_argument(
        "wav_file",
        help="Path to a 16-bit mono WAV file.",
    )
    parser.add_argument(
        "--llm-model",
        help="LM Studio model identifier. Defaults to the first loaded model from `lms ps --json`.",
    )
    parser.add_argument(
        "--lmstudio-base-url",
        default=DEFAULT_LMSTUDIO_BASE_URL,
        help="LM Studio OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--sherpa-model-dir",
        default=str(DEFAULT_SHERPA_MODEL_DIR),
        help="Directory with Sherpa-ONNX Russian ASR model files.",
    )
    parser.add_argument(
        "--num-threads",
        type=int,
        default=2,
        help="CPU threads for Sherpa-ONNX decoding.",
    )
    parser.add_argument(
        "--instructions",
        default="Отвечай по-русски кратко и по делу.",
        help="Instructions sent to the LM Studio /v1/responses endpoint.",
    )
    parser.add_argument(
        "--prompt-template",
        default="Пользователь сказал:\n{transcript}\n\nОтветь на это сообщение.",
        help="Prompt template used after transcription. Use {transcript} as a placeholder.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=512,
        help="Max tokens for the LM Studio response.",
    )
    parser.add_argument(
        "--transcript-only",
        action="store_true",
        help="Only print the transcript and skip LM Studio.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a formatted report.",
    )
    return parser.parse_args()


def fail(message: str) -> None:
    raise SystemExit(message)


def choose_existing_file(model_dir: Path, *candidates: str) -> Path:
    for candidate in candidates:
        path = model_dir / candidate
        if path.is_file():
            return path
    fail(
        f"Model file not found in {model_dir}: tried {', '.join(candidates)}"
    )


def load_wav_as_floats(wav_path: Path) -> tuple[int, list[float]]:
    if not wav_path.is_file():
        fail(f"WAV file not found: {wav_path}")

    with wave.open(str(wav_path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())

    if channels != 1:
        fail(f"Only mono WAV is supported, got {channels} channels: {wav_path}")

    if sample_width != 2:
        fail(
            f"Only 16-bit PCM WAV is supported, got sample width {sample_width} bytes: {wav_path}"
        )

    pcm = array("h")
    pcm.frombytes(frames)
    if sys.byteorder == "big":
        pcm.byteswap()

    samples = [value / 32768.0 for value in pcm]
    return sample_rate, samples


def create_recognizer(model_dir: Path, num_threads: int) -> sherpa_onnx.OfflineRecognizer:
    if not model_dir.is_dir():
        fail(f"Sherpa model directory not found: {model_dir}")

    tokens = choose_existing_file(model_dir, "tokens.txt")
    encoder = choose_existing_file(model_dir, "encoder.int8.onnx", "encoder.onnx")
    decoder = choose_existing_file(model_dir, "decoder.onnx", "decoder.int8.onnx")
    joiner = choose_existing_file(model_dir, "joiner.int8.onnx", "joiner.onnx")

    return sherpa_onnx.OfflineRecognizer.from_transducer(
        tokens=str(tokens),
        encoder=str(encoder),
        decoder=str(decoder),
        joiner=str(joiner),
        num_threads=num_threads,
    )


def transcribe_wav(
    recognizer: sherpa_onnx.OfflineRecognizer,
    wav_path: Path,
) -> dict:
    sample_rate, samples = load_wav_as_floats(wav_path)
    stream = recognizer.create_stream()
    stream.accept_waveform(sample_rate, samples)
    recognizer.decode_stream(stream)
    result = json.loads(str(stream.result))
    result["text"] = result.get("text", "").strip()
    return result


def http_request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
) -> dict:
    data = None
    headers = {"Accept": "application/json"}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        fail(f"HTTP {error.code} from {url}: {body}")
    except urllib.error.URLError as error:
        fail(f"Failed to reach {url}: {error.reason}")


def detect_loaded_lmstudio_model() -> str | None:
    try:
        completed = subprocess.run(
            ["lms", "ps", "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    try:
        models = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None

    for model in models:
        if model.get("type") == "llm" and model.get("identifier"):
            return model["identifier"]

    return None


def detect_any_lmstudio_model(base_url: str) -> str | None:
    models_url = f"{base_url.rstrip('/')}/models"
    response = http_request_json(models_url)
    for model in response.get("data", []):
        model_id = model.get("id")
        if model_id:
            return model_id
    return None


def resolve_lmstudio_model(explicit_model: str | None, base_url: str) -> str:
    if explicit_model:
        return explicit_model

    detected = detect_loaded_lmstudio_model()
    if detected:
        return detected

    detected = detect_any_lmstudio_model(base_url)
    if detected:
        return detected

    fail(
        "No LM Studio model found. Load a model in LM Studio or pass --llm-model explicitly."
    )


def extract_output_text(response: dict) -> str:
    texts: list[str] = []
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                texts.append(content.get("text", ""))

    if texts:
        return "".join(texts).strip()

    return ""


def ask_lmstudio(
    *,
    base_url: str,
    model: str,
    transcript: str,
    instructions: str,
    prompt_template: str,
    max_output_tokens: int,
) -> dict:
    prompt = prompt_template.format(transcript=transcript)
    payload = {
        "model": model,
        "input": prompt,
        "instructions": instructions,
        "max_output_tokens": max_output_tokens,
    }
    responses_url = f"{base_url.rstrip('/')}/responses"
    response = http_request_json(responses_url, method="POST", payload=payload)
    response["output_text"] = extract_output_text(response)
    return response


def main() -> None:
    args = parse_args()

    wav_path = Path(args.wav_file).expanduser().resolve()
    model_dir = Path(args.sherpa_model_dir).expanduser().resolve()
    recognizer = create_recognizer(model_dir=model_dir, num_threads=args.num_threads)

    transcription = transcribe_wav(recognizer=recognizer, wav_path=wav_path)
    transcript = transcription.get("text", "")

    if not transcript:
        fail("Sherpa-ONNX returned an empty transcript.")

    payload = {
        "wav_file": str(wav_path),
        "sherpa_model_dir": str(model_dir),
        "transcript": transcript,
        "transcription": transcription,
    }

    if not args.transcript_only:
        model = resolve_lmstudio_model(args.llm_model, args.lmstudio_base_url)
        response = ask_lmstudio(
            base_url=args.lmstudio_base_url,
            model=model,
            transcript=transcript,
            instructions=args.instructions,
            prompt_template=args.prompt_template,
            max_output_tokens=args.max_output_tokens,
        )
        payload["lmstudio_model"] = model
        payload["response"] = response.get("output_text", "")
        payload["raw_response"] = response

    if args.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    print(f"WAV: {wav_path}")
    print(f"Transcript: {transcript}")

    if args.transcript_only:
        return

    print(f"LM Studio model: {payload['lmstudio_model']}")
    print("Answer:")
    print(payload["response"])


if __name__ == "__main__":
    main()
