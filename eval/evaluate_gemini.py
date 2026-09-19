#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import getpass
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional


try:
    from google import genai
    from google.genai import types
except ModuleNotFoundError:
    genai = None
    types = None


DEFAULT_MODEL_NAME = "gemini-3.1-pro-preview"
DEFAULT_INDEX_PROMPTS = Path("./prompts/index_elements_prompts.txt")
EVALUATION_CRITERIA_JSON = Path(__file__).resolve().parent / "evaluation_criteria.json"
# Two files are written: <prefix>_raw.jsonl (every record exactly as evaluated) and
# <prefix>_audiofiltered.jsonl (one record per video, 2b set to NA outside the audio scope).
DEFAULT_OUTPUT_PREFIX = "./gemini_video_culture_eval_results"

TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 8000  # includes Gemini's thinking tokens
RETRIES = 3  # retries after an invalid JSON response
FILE_TIMEOUT_SEC = 300
FILE_POLL_INTERVAL_SEC = 5
PRINT_SAMPLE_LIMIT = 100

# Videos are grouped by generation model because not every model generates audio,
# which matters for 2b (Audio Cultural Alignment); see AUDIO_MODEL_NAMES below.
MODEL_DIRS = {
    "cosmos2.5": Path("./video_generate/cosmos2.5"),
    "kling3.0": Path("./video_generate/kling3.0"),
    "veo3.1": Path("./video_generate/veo3.1"),
    "happyhorse1.0": Path("./video_generate/happyhorse1.0"),
    "hunyuan1.5": Path("./video_generate/hunyuan1.5"),
    "ltx2.3": Path("./video_generate/ltx2.3"),
    "wan2.2": Path("./video_generate/wan2.2"),
}

# 2b (Audio Cultural Alignment) is scored for every video, but it should only be used
# for videos from models that generate audio and whose aspect (digits 3-4 of the
# video ID) is audio-related: 07 Celebrations, 08 Dancing, 11 Concert, 12 Wedding,
# 13 Funeral, 14 Religious Activity.
AUDIO_MODEL_NAMES = {"kling3.0", "veo3.1", "happyhorse1.0", "ltx2.3"}
AUDIO_ASPECT_CODES = {"07", "08", "11", "12", "13", "14"}

COUNTRY_BY_PREFIX = {
    "01": "China",
    "02": "Japan",
    "03": "India",
    "04": "Malaysia",
    "05": "Nigeria",
    "06": "Ethiopia",
    "07": "Russia",
    "08": "Italy",
    "09": "Netherlands",
    "10": "United States",
    "11": "New Zealand",
    "12": "Brazil",
}

JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
SCORE_VALUES = ["1", "2", "3", "4", "5", "NA"]


# Rubric of the eight criteria (question, description, and the meaning of each score).
EVALUATION_CRITERIA = json.loads(EVALUATION_CRITERIA_JSON.read_text(encoding="utf-8"))

OUTPUT_JSON_TEMPLATE = json.dumps(
    {
        "1a": {
            "requirement": "Expected cultural element.",
            "observation": "Visible evidence.",
            "justification": "Brief reason.",
            "score": 1,
        },
        "1b": {
            "observation": "Visible foreign or culturally inconsistent elements, or none.",
            "justification": "Brief reason.",
            "score": 1,
        },
        "2a": {
            "observation": "Visible text if any, otherwise empty string.",
            "justification": "Brief reason, or why NA is selected.",
            "score": 1,
        },
        "2b": {
            "audio_observation": "Audible speech, chanting, singing, music, instruments, rhythm, ambient sound, or silence.",
            "justification": "Brief reason, or why NA is selected.",
            "score": 1,
        },
        "3a": {
            "requirement": "Expected subject or participants.",
            "observation": "Visible subject or participants.",
            "justification": "Brief reason.",
            "score": 1,
        },
        "3b": {
            "requirement": "Expected action.",
            "observation": "Visible action across frames.",
            "justification": "Brief reason.",
            "score": 1,
        },
        "4a": {
            "observation": "Visible realism issues or realistic aspects.",
            "justification": "Brief reason.",
            "score": 1,
        },
        "4b": {
            "observation": "Visible visual quality issues.",
            "justification": "Brief reason.",
            "score": 1,
        },
    },
    ensure_ascii=False,
    indent=2,
)


def metric_schema(required_fields):
    properties = {
        field: {"type": "STRING"}
        for field in required_fields
        if field != "score"
    }
    properties["score"] = {
        "type": "STRING",
        "enum": SCORE_VALUES,
    }
    return {
        "type": "OBJECT",
        "properties": properties,
        "required": required_fields,
    }


EVALUATION_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "1a": metric_schema(["requirement", "observation", "justification", "score"]),
        "1b": metric_schema(["observation", "justification", "score"]),
        "2a": metric_schema(["observation", "justification", "score"]),
        "2b": metric_schema(["audio_observation", "justification", "score"]),
        "3a": metric_schema(["requirement", "observation", "justification", "score"]),
        "3b": metric_schema(["requirement", "observation", "justification", "score"]),
        "4a": metric_schema(["observation", "justification", "score"]),
        "4b": metric_schema(["observation", "justification", "score"]),
    },
    "required": ["1a", "1b", "2a", "2b", "3a", "3b", "4a", "4b"],
}

def normalize_score_values(result: dict) -> dict:
    for metric in ["1a", "1b", "2a", "2b", "3a", "3b", "4a", "4b"]:
        item = result.get(metric)
        if not isinstance(item, dict):
            continue
        score = item.get("score")
        if isinstance(score, str) and score != "NA":
            try:
                item["score"] = int(score)
            except ValueError:
                pass
    return result


def criteria_to_text() -> str:
    lines = []
    for key in ["1a", "1b", "2a", "2b", "3a", "3b", "4a", "4b"]:
        c = EVALUATION_CRITERIA[key]
        lines.append(f"{key}: {c['question']} ({c['category']})")
        lines.append(f"Description: {c['description']}")
        lines.append("Scoring:")
        for score, desc in c["scoring"].items():
            lines.append(f"  {score}: {desc}")
        lines.append("")
    return "\n".join(lines)


def is_audio_in_scope(model_name: str, video_id: str) -> bool:
    return model_name in AUDIO_MODEL_NAMES and video_id[2:4] in AUDIO_ASPECT_CODES


def country_from_video_id(video_id: str) -> str:
    return COUNTRY_BY_PREFIX.get(video_id[:2], "")


def load_index_prompts(path: Path) -> dict:
    metadata = {}
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split("\t")
            if len(parts) != 3:
                raise ValueError(f"{path} line {line_number} must have 3 tab-separated columns")

            video_id, cultural_element, prompt = parts
            metadata[video_id] = {
                "video_id": video_id,
                "country": country_from_video_id(video_id),
                "cultural_element": cultural_element,
                "prompt": prompt,
            }
    return metadata


def find_video_file(model_name: str, video_id: str) -> Optional[Path]:
    model_dir = MODEL_DIRS[model_name]
    matches = sorted(model_dir.glob(f"{video_id}_*.mp4"))
    if len(matches) > 1:
        raise RuntimeError(f"Multiple videos found for {model_name} {video_id}: {matches}")
    return matches[0] if matches else None


def build_samples(metadata: dict) -> tuple:
    samples = []
    missing = []
    for video_id in sorted(metadata):
        item = metadata[video_id]
        for model_name in MODEL_DIRS:
            video_path = find_video_file(model_name, item["video_id"])
            if video_path is None:
                missing.append((model_name, item["video_id"]))
                continue
            sample = dict(item)
            sample["model_name"] = model_name
            sample["video_path"] = video_path
            sample["custom_id"] = f"{model_name}_{item['video_id']}"
            sample["audio_in_scope"] = is_audio_in_scope(model_name, item["video_id"])
            samples.append(sample)

    return samples, missing


def load_run_state(path: Path) -> dict:
    state = {
        "completed": set(),
        "failed": set(),
        "malformed_lines": 0,
        "records": 0,
    }
    if not path.exists():
        return state

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            state["records"] += 1
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                state["malformed_lines"] += 1
                continue

            custom_id = item.get("custom_id")
            if not custom_id:
                continue

            if "result" in item:
                state["completed"].add(custom_id)
                state["failed"].discard(custom_id)
            elif "error" in item and custom_id not in state["completed"]:
                state["failed"].add(custom_id)

    return state


def build_prompt(sample: dict) -> str:
    return f"""You are an expert evaluator for cultural capability in text-to-video generation.

You are given a 5-second AI-generated video with visual content and possible audio.
Your task is to evaluate how well the video matches the prompt and the target cultural context.

Metadata:
- Target country/culture: {sample["country"]}
- Cultural element: {sample["cultural_element"]}
- Video prompt: {sample["prompt"]}

Rules:
- Evaluate the video itself: use the visual content for visual criteria and listen to the audio for 2b.
- Use the full 1-5 scale.
- For text rendering, use "NA" if no visible text appears.
- For audio cultural alignment, use "NA" only if the video has no audible audio, or if the audio is purely non-cultural ambient sound.
- Evaluate each question independently.
- The requirement fields should describe the evaluation target, not the model's visual observation.
- The observation fields should describe evidence from the video. For 2b, describe audible evidence.

Evaluation criteria:
{criteria_to_text()}

Output Format:
Return strictly valid JSON only. Do not include markdown, explanations outside JSON, or extra text.
{OUTPUT_JSON_TEMPLATE}
"""


def ascii_safe_upload_path(video_path: Path, custom_id: str):
    try:
        str(video_path).encode("ascii")
        return video_path, None
    except UnicodeEncodeError:
        temp_dir = tempfile.TemporaryDirectory(prefix="gemini_upload_")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", custom_id) + video_path.suffix
        safe_path = Path(temp_dir.name) / safe_name
        shutil.copy2(video_path, safe_path)
        return safe_path, temp_dir


def wait_for_file_ready(client, uploaded_file, timeout_sec: int, poll_interval_sec: int):
    start = time.time()
    current = uploaded_file
    while True:
        state = getattr(current, "state", None)
        state_name = getattr(state, "name", str(state))
        if state_name in {"ACTIVE", "FileState.ACTIVE"}:
            return current
        if state_name in {"FAILED", "FileState.FAILED"}:
            raise RuntimeError(f"Uploaded file processing failed: {uploaded_file}")
        if time.time() - start > timeout_sec:
            raise TimeoutError(f"Timed out waiting for Gemini file processing: {uploaded_file}")
        time.sleep(poll_interval_sec)
        current = client.files.get(name=current.name)


def response_usage(response) -> dict:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if hasattr(usage, "to_json_dict"):
        return usage.to_json_dict()
    return {k: v for k, v in vars(usage).items() if not k.startswith("_")}


def parse_json_response(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = JSON_OBJECT_RE.search(text)
        if not match:
            raise
        return json.loads(match.group(0))


def extract_result(response) -> dict:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, dict):
        return normalize_score_values(parsed)
    return normalize_score_values(parse_json_response(response.text))


def is_invalid_api_key_error(exc: Exception) -> bool:
    text = str(exc)
    return "API_KEY_INVALID" in text or "API key not valid" in text


def is_model_not_found_error(exc: Exception) -> bool:
    text = str(exc)
    return "NOT_FOUND" in text and "models/" in text and "generateContent" in text


def evaluate_sample(client, sample: dict, model_name: str) -> dict:
    uploaded_file = None
    upload_temp_dir = None
    record = None
    delete_error = None
    try:
        upload_path, upload_temp_dir = ascii_safe_upload_path(sample["video_path"], sample["custom_id"])
        uploaded_file = client.files.upload(file=str(upload_path))
        uploaded_file = wait_for_file_ready(
            client,
            uploaded_file,
            timeout_sec=FILE_TIMEOUT_SEC,
            poll_interval_sec=FILE_POLL_INTERVAL_SEC,
        )

        total_elapsed_sec = 0.0
        for attempt in range(1, RETRIES + 2):
            start = time.time()
            response = client.models.generate_content(
                model=model_name,
                contents=[uploaded_file, build_prompt(sample)],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=EVALUATION_RESPONSE_SCHEMA,
                    temperature=TEMPERATURE,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
            elapsed_sec = round(time.time() - start, 3)
            total_elapsed_sec += elapsed_sec

            try:
                result = extract_result(response)
                break
            except (json.JSONDecodeError, TypeError) as exc:
                if attempt > RETRIES:
                    raise
                print(f"Retrying {sample['custom_id']} after invalid JSON ({attempt}/{RETRIES}): {exc}")

        record = {
            "custom_id": sample["custom_id"],
            "video_id": sample["video_id"],
            "model_name": sample["model_name"],
            "country": sample["country"],
            "cultural_element": sample["cultural_element"],
            "prompt": sample["prompt"],
            "video_path": str(sample["video_path"]),
            "audio_in_scope": sample["audio_in_scope"],
            "evaluator_model": model_name,
            "result": result,
            "usage": response_usage(response),
            "elapsed_sec": elapsed_sec,
            "total_elapsed_sec": round(total_elapsed_sec, 3),
        }
        return record
    finally:
        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception as exc:
                delete_error = str(exc)
        if upload_temp_dir is not None:
            upload_temp_dir.cleanup()
        if record is not None and delete_error:
            record["uploaded_file_delete_error"] = delete_error


def write_audio_filtered(raw_path: Path, filtered_path: Path) -> int:
    """Rebuild the audio-filtered file from the raw file.

    Keeps the latest successful record of each custom_id, drops error-only records,
    and sets 2b to NA for records outside the audio evaluation scope.
    """
    records = {}
    if raw_path.exists():
        with raw_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and item.get("custom_id") and "result" in item:
                    records[item["custom_id"]] = item

    filtered_path.parent.mkdir(parents=True, exist_ok=True)
    with filtered_path.open("w", encoding="utf-8") as f:
        for custom_id in sorted(records):
            item = records[custom_id]
            if not item.get("audio_in_scope", is_audio_in_scope(item["model_name"], item["video_id"])):
                item["result"]["2b"] = {
                    "audio_observation": "",
                    "justification": "Outside the audio evaluation scope.",
                    "score": "NA",
                }
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return len(records)


def write_jsonl_record(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def get_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if api_key:
        return api_key
    return getpass.getpass("Enter Gemini API key: ").strip()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Evaluate culture-related criteria of generated videos with Gemini for all prompts in the index file."
    )
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME, help=f"Gemini evaluator model. Default: {DEFAULT_MODEL_NAME}")
    parser.add_argument("--index-prompts", type=Path, default=DEFAULT_INDEX_PROMPTS)
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX, help="Results are written to <prefix>_raw.jsonl and <prefix>_audiofiltered.jsonl.")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate at most this many pending videos.")
    parser.add_argument("--dry-run", action="store_true", help="List selected videos without calling Gemini.")
    parser.add_argument("--overwrite", action="store_true", help="Ignore existing output and re-evaluate all selected videos.")
    return parser.parse_args()


def main():
    args = parse_args()
    raw_jsonl = Path(f"{args.output_prefix}_raw.jsonl")
    filtered_jsonl = Path(f"{args.output_prefix}_audiofiltered.jsonl")

    metadata = load_index_prompts(args.index_prompts)
    samples, missing = build_samples(metadata)

    if args.overwrite:
        run_state = {
            "completed": set(),
            "failed": set(),
            "malformed_lines": 0,
            "records": 0,
        }
    else:
        run_state = load_run_state(raw_jsonl)

    completed = run_state["completed"]
    failed = run_state["failed"]
    pending = [sample for sample in samples if sample["custom_id"] not in completed]
    if args.limit > 0:
        pending = pending[: args.limit]

    print(f"Found videos: {len(samples)}")
    print(f"Missing videos: {len(missing)}")
    if missing:
        print("First missing videos:")
        for model_name, video_id in missing[:20]:
            print(f"  {model_name} {video_id}")
    sample_custom_ids = {sample["custom_id"] for sample in samples}
    print(f"Output records: {run_state['records']}")
    print(f"Already completed: {len(completed & sample_custom_ids)}")
    print(f"Previous errors to retry: {len(failed & sample_custom_ids)}")
    if run_state["malformed_lines"]:
        print(f"Malformed output lines ignored: {run_state['malformed_lines']}")
    print(f"Pending this run: {len(pending)}")
    print(f"Raw output: {raw_jsonl}")
    print(f"Audio-filtered output: {filtered_jsonl}")
    print(f"Evaluator model: {args.model}")

    pending_custom_ids = {sample["custom_id"] for sample in pending}
    printed = 0
    for sample in samples:
        if sample["custom_id"] in completed:
            status = "done/skip"
        elif sample["custom_id"] in pending_custom_ids:
            status = "retry" if sample["custom_id"] in failed else "pending"
        else:
            status = "pending+"
        print(f"{status:9s} {sample['custom_id']:24s} {sample['video_path']}")
        printed += 1
        if printed >= PRINT_SAMPLE_LIMIT:
            remaining_to_print = len(samples) - printed
            if remaining_to_print > 0:
                print(f"... {remaining_to_print} more sample paths not shown.")
            break

    if args.dry_run:
        return 0

    if genai is None or types is None:
        print(
            "Missing dependency: google-genai\n"
            "Install it with:\n"
            "  python3 -m pip install google-genai",
            file=sys.stderr,
        )
        return 2

    if args.overwrite and raw_jsonl.exists():
        raw_jsonl.unlink()

    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("Gemini API key is required.")
    client = genai.Client(api_key=api_key)

    try:
        for index, sample in enumerate(pending, start=1):
            print(f"\n[{index}/{len(pending)}] Evaluating {sample['custom_id']}")
            try:
                record = evaluate_sample(client, sample, args.model)
            except Exception as exc:
                if is_invalid_api_key_error(exc):
                    print(f"ERROR {sample['custom_id']}: {exc}")
                    print("Stopping because the Gemini API key is invalid. Set a valid GEMINI_API_KEY, then rerun.")
                    return 1
                if is_model_not_found_error(exc):
                    print(f"ERROR {sample['custom_id']}: {exc}")
                    print("Stopping because the evaluator model is not available for generateContent. Pass --model with a supported model name.")
                    return 1

                error_record = {
                    "custom_id": sample["custom_id"],
                    "video_id": sample["video_id"],
                    "model_name": sample["model_name"],
                    "country": sample["country"],
                    "cultural_element": sample["cultural_element"],
                    "prompt": sample["prompt"],
                    "video_path": str(sample["video_path"]),
                    "audio_in_scope": sample["audio_in_scope"],
                    "evaluator_model": args.model,
                    "error": str(exc),
                }
                write_jsonl_record(raw_jsonl, error_record)
                print(f"ERROR {sample['custom_id']}: {exc}")
                continue

            write_jsonl_record(raw_jsonl, record)
            scores = {metric: item.get("score") for metric, item in record["result"].items()}
            print(f"Saved {sample['custom_id']} scores={scores}")
    finally:
        count = write_audio_filtered(raw_jsonl, filtered_jsonl)
        print(f"\nWrote {count} audio-filtered records to {filtered_jsonl}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
