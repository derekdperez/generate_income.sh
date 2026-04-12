#!/usr/bin/env python3
"""Parameter permutation and fuzz runner for Nightmare parameter inventories.

Usage:
    python fozzy.py output/example.com/example.com.parameters.json
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import itertools
import json
import re
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable


DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_DELAY_SECONDS = 0.1
DEFAULT_MAX_PERMUTATIONS = 512
LIVE_REPORT_INTERVAL_SECONDS = 5.0
BODY_PREVIEW_LIMIT = 2048
REQUEST_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

def _http_status_phrase(code: int) -> str:
    try:
        return HTTPStatus(code).phrase
    except ValueError:
        return "Unknown"


DEFAULT_GENERIC_BY_TYPE: dict[str, str] = {
    "bool": "true",
    "int": "1",
    "float": "1.0",
    "uuid": "00000000-0000-4000-8000-000000000000",
    "date": "2024-01-01",
    "datetime": "2024-01-01T00:00:00Z",
    "email": "test@example.com",
    "url": "https://example.com",
    "hex": "deadbeef",
    "token": "sampletoken",
    "empty": "",
    "string": "test",
}

TYPE_PRIORITY = ["uuid", "datetime", "date", "email", "url", "bool", "float", "int", "hex", "token", "empty", "string"]


@dataclass
class ParameterMeta:
    name: str
    data_type: str = "string"
    observed_values: set[str] = field(default_factory=set)


@dataclass
class RouteGroup:
    host: str
    path: str
    scheme: str
    observed_param_sets: set[frozenset[str]] = field(default_factory=set)
    params: dict[str, ParameterMeta] = field(default_factory=dict)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read a Nightmare parameters.json file, derive valid/optional parameter combinations, "
            "generate placeholder permutations, and run baseline-vs-fuzz requests."
        )
    )
    parser.add_argument("parameters_file", help="Path to <domain>.parameters.json")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="Request timeout in seconds")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="Delay between requests in seconds")
    parser.add_argument(
        "--max-permutations",
        type=int,
        default=DEFAULT_MAX_PERMUTATIONS,
        help="Maximum permutations per host/path",
    )
    parser.add_argument(
        "--quick-fuzz-list",
        default="resources/quick_fuzz_list.txt",
        help=(
            "Path to quick fuzz list file. "
            "If omitted and not found, also tries resources/wordlists/quick_fuzz_list.txt."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for fozzy artifacts. Defaults to sibling 'fozzy-output/<root-domain>'.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate combinations/placeholders only; do not perform network requests.",
    )
    return parser.parse_args()


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def merge_data_type(existing: str, new_type: str) -> str:
    if not existing:
        return new_type or "string"
    if not new_type:
        return existing
    existing = existing.strip().lower()
    new_type = new_type.strip().lower()
    if existing == new_type:
        return existing
    if existing == "string" or new_type == "string":
        return "string"
    if {"float", "int"} == {existing, new_type}:
        return "float"
    for candidate in TYPE_PRIORITY:
        if candidate in {existing, new_type}:
            return candidate
    return "string"


def infer_value_type(value: str) -> str:
    text = (value or "").strip()
    if text == "":
        return "empty"
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return "bool"
    if re.fullmatch(r"[+-]?\d+", text):
        return "int"
    if re.fullmatch(r"[+-]?\d+\.\d+", text):
        return "float"
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", text):
        return "uuid"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return "date"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[^ ]+", text):
        return "datetime"
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
        return "email"
    if re.fullmatch(r"https?://\S+", text, flags=re.IGNORECASE):
        return "url"
    if re.fullmatch(r"[0-9a-fA-F]+", text) and len(text) % 2 == 0:
        return "hex"
    if re.fullmatch(r"[A-Za-z0-9_-]{8,}={0,2}", text):
        return "token"
    return "string"


def load_route_groups(parameters_payload: dict[str, Any]) -> list[RouteGroup]:
    grouped: dict[tuple[str, str], RouteGroup] = {}
    entries = parameters_payload.get("entries", [])
    if not isinstance(entries, list):
        return []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_url = str(entry.get("url", "")).strip()
        if not raw_url:
            continue
        parsed = urllib.parse.urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        host = parsed.netloc.lower()
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/") or "/"
        key = (host, path)
        if key not in grouped:
            grouped[key] = RouteGroup(host=host, path=path, scheme=parsed.scheme.lower())
        group = grouped[key]

        # Observed parameter set from URL query.
        query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        observed_keys = frozenset(key for key, _ in query_items if key)
        if observed_keys:
            group.observed_param_sets.add(observed_keys)

        parameters = entry.get("parameters", [])
        if isinstance(parameters, list):
            for param in parameters:
                if not isinstance(param, dict):
                    continue
                name = str(param.get("name", "")).strip()
                if not name:
                    continue
                dtype = str(param.get("canonical_data_type", "string")).strip().lower() or "string"
                meta = group.params.get(name)
                if meta is None:
                    meta = ParameterMeta(name=name, data_type=dtype)
                    group.params[name] = meta
                else:
                    meta.data_type = merge_data_type(meta.data_type, dtype)
                observed_values = param.get("observed_values", [])
                if isinstance(observed_values, list):
                    for value in observed_values:
                        meta.observed_values.add(str(value))

        # Backfill param metadata from URL query values.
        for key_name, value in query_items:
            if not key_name:
                continue
            meta = group.params.get(key_name)
            inferred = infer_value_type(value)
            if meta is None:
                meta = ParameterMeta(name=key_name, data_type=inferred)
                group.params[key_name] = meta
            else:
                meta.data_type = merge_data_type(meta.data_type, inferred)
            meta.observed_values.add(value)

    return [grouped[key] for key in sorted(grouped.keys())]


def generic_value_for(meta: ParameterMeta) -> str:
    return DEFAULT_GENERIC_BY_TYPE.get(meta.data_type, "test")


def baseline_seed_value_for(meta: ParameterMeta) -> str:
    observed = sorted({str(value) for value in meta.observed_values if str(value) != ""}, key=lambda item: (len(item), item))
    if observed:
        return observed[0]
    return generic_value_for(meta)


def load_quick_fuzz_values(quick_fuzz_path: str) -> list[str]:
    candidates = [Path(quick_fuzz_path)]
    if quick_fuzz_path == "resources/quick_fuzz_list.txt":
        candidates.append(Path("resources/wordlists/quick_fuzz_list.txt"))
    resolved: Path | None = None
    for candidate in candidates:
        if candidate.exists():
            resolved = candidate
            break
    if resolved is None:
        raise FileNotFoundError(
            f"Quick fuzz list not found. Tried: {', '.join(str(path) for path in candidates)}"
        )
    lines = resolved.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        raise ValueError(f"Quick fuzz list is empty: {resolved}")
    return lines


def build_url(scheme: str, host: str, path: str, values: dict[str, str]) -> str:
    query = urllib.parse.urlencode([(name, values[name]) for name in sorted(values.keys())], doseq=True)
    return urllib.parse.urlunparse((scheme, host, path, "", query, ""))


def build_placeholder_url(scheme: str, host: str, path: str, params: list[ParameterMeta]) -> str:
    counters: dict[str, int] = defaultdict(int)
    pairs: list[str] = []
    for param in sorted(params, key=lambda item: item.name):
        dtype = param.data_type or "string"
        counters[dtype] += 1
        placeholder = f"{{{dtype}{counters[dtype]}}}"
        key_encoded = urllib.parse.quote_plus(param.name, safe="[]")
        pairs.append(f"{key_encoded}={placeholder}")
    query = "&".join(pairs)
    return urllib.parse.urlunparse((scheme, host, path, "", query, ""))


def request_url(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers=REQUEST_HEADERS,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read(BODY_PREVIEW_LIMIT)
            return {
                "url": url,
                "status": int(getattr(response, "status", 0) or 0),
                "size": len(body),
                "body_preview": body.decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(BODY_PREVIEW_LIMIT)
        except Exception:
            body = b""
        return {
            "url": url,
            "status": int(getattr(exc, "code", 0) or 0),
            "size": len(body),
            "body_preview": body.decode("utf-8", errors="replace"),
        }
    except Exception as exc:
        return {
            "url": url,
            "status": 0,
            "size": 0,
            "body_preview": "",
            "error": str(exc),
        }


def response_differs(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return int(a.get("status", 0)) != int(b.get("status", 0)) or int(a.get("size", 0)) != int(b.get("size", 0))


def curl_command_for_url(url: str, timeout: float) -> str:
    timeout_token = str(int(timeout)) if timeout > 0 else "10"
    parts = ["curl", "-sS", "-L", "--max-time", timeout_token]
    for key, value in REQUEST_HEADERS.items():
        parts.extend(["-H", f"\"{key}: {value}\""])
    parts.append(f"\"{url}\"")
    return " ".join(parts)


def empty_expected_response() -> dict[str, Any]:
    return {"status": None, "size": None, "body_preview": None}


def make_request_key(
    *,
    phase: str,
    host: str,
    path: str,
    permutation: tuple[str, ...] | list[str] | None,
    mutated_parameter: str | None,
    mutated_value: str | None,
    url: str,
) -> str:
    payload = {
        "phase": str(phase or "").strip(),
        "host": str(host or "").strip().lower(),
        "path": str(path or "").strip() or "/",
        "permutation": list(permutation or ()),
        "mutated_parameter": str(mutated_parameter or ""),
        "mutated_value": str(mutated_value or ""),
        "url": str(url or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_key_from_plan_line(line: dict[str, Any]) -> str:
    return make_request_key(
        phase=str(line.get("phase", "")),
        host=str(line.get("host", "")),
        path=str(line.get("path", "")),
        permutation=list(line.get("permutation", [])) if isinstance(line.get("permutation"), list) else [],
        mutated_parameter=str(line.get("mutated_parameter", "") or ""),
        mutated_value=str(line.get("mutated_value", "") or ""),
        url=str(line.get("url", "")),
    )


def load_resume_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"requested": {}, "queued": [], "meta": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"requested": {}, "queued": [], "meta": {}}
    if not isinstance(payload, dict):
        return {"requested": {}, "queued": [], "meta": {}}
    requested = payload.get("requested", {})
    queued = payload.get("queued", [])
    meta = payload.get("meta", {})
    return {
        "requested": requested if isinstance(requested, dict) else {},
        "queued": queued if isinstance(queued, list) else [],
        "meta": meta if isinstance(meta, dict) else {},
    }


def save_resume_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def queue_dry_run_request(
    plan_lines: list[dict[str, Any]] | None,
    phase: str,
    url: str,
    timeout: float,
    host: str,
    path: str,
    permutation: tuple[str, ...] | None,
    mutated_parameter: str | None,
    mutated_value: str | None,
    expected_baseline_response: dict[str, Any],
) -> None:
    if plan_lines is None:
        return
    request_id = len(plan_lines) + 1
    line = {
        "request_id": request_id,
        "phase": phase,
        "host": host,
        "path": path,
        "permutation": list(permutation or ()),
        "mutated_parameter": mutated_parameter,
        "mutated_value": mutated_value,
        "url": url,
        "curl": curl_command_for_url(url, timeout=timeout),
        "expected_baseline_response": expected_baseline_response,
        "actual_response": {},
    }
    line["request_key"] = request_key_from_plan_line(line)
    plan_lines.append(line)


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def build_endpoint_request_counts(plan_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for line in plan_lines:
        host = str(line.get("host", "")).strip().lower()
        path = str(line.get("path", "")).strip() or "/"
        if not host:
            continue
        endpoint = f"{host}{path}"
        counts[endpoint] = counts.get(endpoint, 0) + 1
    rows = [{"endpoint": endpoint, "request_count": count} for endpoint, count in counts.items()]
    rows.sort(key=lambda item: (-int(item["request_count"]), str(item["endpoint"])))
    return rows


def list_results_folder_files(results_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not results_dir.exists():
        return records
    for path in sorted(results_dir.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        stat = path.stat()
        records.append(
            {
                "name": path.name,
                "path": str(path),
                "extension": path.suffix.lower(),
                "size_bytes": int(stat.st_size),
                "modified_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return records


def infer_required_and_optional(
    group: RouteGroup,
    timeout: float,
    delay: float,
    dry_run: bool,
    dry_run_plan_lines: list[dict[str, Any]] | None = None,
) -> tuple[set[str], set[str]]:
    all_params = set(group.params.keys())
    if not all_params:
        return set(), set()

    if group.observed_param_sets:
        observed_list = list(group.observed_param_sets)
        required = set(observed_list[0])
        for values in observed_list[1:]:
            required &= set(values)
    else:
        required = set()
    optional = set(all_params - required)

    baseline_values = {name: baseline_seed_value_for(group.params[name]) for name in sorted(all_params)}
    baseline_url = build_url(group.scheme, group.host, group.path, baseline_values)
    baseline_ref = {
        **empty_expected_response(),
        "source": "requiredness_baseline",
        "note": "Populate from the matching baseline request response when executing this plan.",
    }
    if dry_run:
        queue_dry_run_request(
            plan_lines=dry_run_plan_lines,
            phase="requiredness_baseline",
            url=baseline_url,
            timeout=timeout,
            host=group.host,
            path=group.path,
            permutation=tuple(sorted(all_params)),
            mutated_parameter=None,
            mutated_value=None,
            expected_baseline_response=baseline_ref,
        )
        for candidate in sorted(optional):
            trial_values = dict(baseline_values)
            trial_values.pop(candidate, None)
            trial_url = build_url(group.scheme, group.host, group.path, trial_values)
            queue_dry_run_request(
                plan_lines=dry_run_plan_lines,
                phase="requiredness_probe_remove_parameter",
                url=trial_url,
                timeout=timeout,
                host=group.host,
                path=group.path,
                permutation=tuple(sorted(trial_values.keys())),
                mutated_parameter=candidate,
                mutated_value=None,
                expected_baseline_response=baseline_ref,
            )
        return required, optional

    if not optional:
        return required, optional

    baseline = request_url(baseline_url, timeout=timeout)
    time.sleep(delay)

    if int(baseline.get("status", 0)) == 0:
        return required, optional

    for candidate in sorted(optional):
        trial_values = dict(baseline_values)
        trial_values.pop(candidate, None)
        trial_url = build_url(group.scheme, group.host, group.path, trial_values)
        trial = request_url(trial_url, timeout=timeout)
        time.sleep(delay)
        # If response materially changes, treat as required for permutation generation.
        if response_differs(baseline, trial):
            required.add(candidate)

    optional = set(all_params - required)
    return required, optional


def generate_permutations(
    observed_sets: set[frozenset[str]],
    required: set[str],
    optional: set[str],
    max_permutations: int,
) -> list[tuple[str, ...]]:
    ordered: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()

    for observed in sorted(observed_sets, key=lambda items: (len(items), sorted(items))):
        combo = tuple(sorted(observed))
        if combo not in seen:
            seen.add(combo)
            ordered.append(combo)
            if len(ordered) >= max_permutations:
                return ordered

    opt_list = sorted(optional)
    for count in range(0, len(opt_list) + 1):
        for subset in itertools.combinations(opt_list, count):
            combo = tuple(sorted(set(required) | set(subset)))
            if combo not in seen:
                seen.add(combo)
                ordered.append(combo)
                if len(ordered) >= max_permutations:
                    return ordered
    return ordered


def infer_required_optional_from_observed(group: RouteGroup) -> tuple[set[str], set[str]]:
    all_params = set(group.params.keys())
    if not all_params:
        return set(), set()
    if group.observed_param_sets:
        observed_list = list(group.observed_param_sets)
        required = set(observed_list[0])
        for values in observed_list[1:]:
            required &= set(values)
    else:
        required = set()
    optional = set(all_params - required)
    return required, optional


def select_max_parameter_permutations(permutations: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    non_empty = [item for item in permutations if item]
    if not non_empty:
        return []
    max_len = max(len(item) for item in non_empty)
    selected: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for item in non_empty:
        if len(item) != max_len:
            continue
        if item in seen:
            continue
        seen.add(item)
        selected.append(item)
    return selected


def estimate_group_request_count(
    permutations: list[tuple[str, ...]],
    quick_fuzz_values_count: int,
) -> int:
    fuzz_requests = 0
    for permutation in permutations:
        if not permutation:
            continue
        fuzz_requests += 1 + (len(permutation) * quick_fuzz_values_count)
    return fuzz_requests


def anomaly_file_name(host: str, path: str, permutation: tuple[str, ...], parameter: str, value: str) -> str:
    seed = f"{host}|{path}|{','.join(permutation)}|{parameter}|{value}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    path_token = re.sub(r"[^A-Za-z0-9_-]+", "_", path.strip("/")) or "root"
    return f"anomaly_{host}_{path_token}_{digest}.json"


def reflection_file_name(host: str, path: str, permutation: tuple[str, ...], parameter: str, value: str) -> str:
    seed = f"{host}|{path}|{','.join(permutation)}|{parameter}|{value}|reflection"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    path_token = re.sub(r"[^A-Za-z0-9_-]+", "_", path.strip("/")) or "root"
    return f"reflection_{host}_{path_token}_{digest}.json"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def load_result_entries_from_folders(result_dirs: list[Path]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for result_dir in result_dirs:
        if not result_dir.exists():
            continue
        for path in sorted(result_dir.rglob("*.json")):
            name = path.name.lower()
            path_key = str(path.resolve())
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            is_anomaly_name = name.startswith("anomaly_") or name.startswith("anomoly_")
            is_reflection_name = name.startswith("reflection_")
            has_result_shape = isinstance(payload.get("baseline"), dict) and (
                isinstance(payload.get("anomaly"), dict) or isinstance(payload.get("response"), dict)
            )
            if not (is_anomaly_name or is_reflection_name or has_result_shape):
                continue
            baseline = payload.get("baseline", {}) if isinstance(payload.get("baseline"), dict) else {}
            response = payload.get("anomaly", {}) if isinstance(payload.get("anomaly"), dict) else {}
            if not response and isinstance(payload.get("response"), dict):
                response = payload.get("response", {})
            url = str(payload.get("requested_url") or payload.get("url") or "").strip()
            requested_url = str(payload.get("requested_url", "") or "").strip()
            target_url = requested_url or url
            baseline_url = str(baseline.get("url", "") or "").strip()
            response_url = str(response.get("url", "") or "").strip()
            # Some historical files were saved with baseline/anomaly reversed.
            # If baseline points to the mutated requested URL while anomaly does not, swap them.
            if target_url and baseline_url == target_url and response_url and response_url != target_url:
                baseline, response = response, baseline
                baseline_url, response_url = response_url, baseline_url
            # Legacy baseline values that used sampletoken are often invalid.
            # If baseline is an error with sampletoken and anomaly maps to requested_url success, treat as reversed.
            baseline_status_hint = _safe_int(baseline.get("status", 0))
            response_status_hint = _safe_int(response.get("status", 0))
            if (
                target_url
                and response_url == target_url
                and "sampletoken" in baseline_url.lower()
                and baseline_status_hint >= 400
                and 200 <= response_status_hint < 400
            ):
                baseline, response = response, baseline
            captured_at = str(payload.get("captured_at_utc", "")).strip()
            baseline_status = _safe_int(baseline.get("status", 0))
            new_status = _safe_int(response.get("status", 0))
            baseline_size = _safe_int(baseline.get("size", 0))
            new_size = _safe_int(response.get("size", 0))
            baseline_body = str(baseline.get("body_preview", "") or "")
            anomaly_body = str(response.get("body_preview", "") or "")
            diff_lines = list(
                difflib.unified_diff(
                    baseline_body.splitlines(),
                    anomaly_body.splitlines(),
                    fromfile="baseline",
                    tofile="anomaly",
                    lineterm="",
                )
            )
            result_type = "reflection" if is_reflection_name else "anomaly"
            entries.append(
                {
                    "result_type": result_type,
                    "result_file": str(path),
                    "captured_at_utc": captured_at,
                    "url": url,
                    "host": str(payload.get("host", "")),
                    "path": str(payload.get("path", "")),
                    "mutated_parameter": str(payload.get("mutated_parameter", "")),
                    "mutated_value": str(payload.get("mutated_value", "")),
                    "baseline_status": baseline_status,
                    "new_status": new_status,
                    "baseline_size": baseline_size,
                    "new_size": new_size,
                    "size_difference": new_size - baseline_size,
                    "baseline_response": baseline,
                    "anomaly_response": response,
                    "response_diff_text": "\n".join(diff_lines[:400]),
                }
            )
    entries.sort(key=lambda item: (item.get("captured_at_utc", ""), item.get("result_file", "")))
    return entries


def write_results_summary(
    *,
    root_domain: str,
    parameters_path: Path,
    output_dir: Path,
    results_dir: Path,
    interrupted: bool,
    interrupted_group: str | None,
) -> tuple[Path, Path]:
    legacy_anomaly_dir = output_dir / "anomalies"
    scan_dirs = [results_dir]
    if legacy_anomaly_dir.exists():
        scan_dirs.append(legacy_anomaly_dir)
    result_entries = load_result_entries_from_folders(scan_dirs)
    anomaly_entries = [item for item in result_entries if str(item.get("result_type", "")) == "anomaly"]
    reflection_entries = [item for item in result_entries if str(item.get("result_type", "")) == "reflection"]
    unique_urls = sorted({str(item.get("url", "")).strip() for item in result_entries if str(item.get("url", "")).strip()})
    by_url_counter: dict[str, dict[str, Any]] = {}
    status_code_changes = 0
    size_changes = 0
    for item in result_entries:
        baseline_status = _safe_int(item.get("baseline_status", 0))
        new_status = _safe_int(item.get("new_status", 0))
        baseline_size = _safe_int(item.get("baseline_size", 0))
        new_size = _safe_int(item.get("new_size", 0))
        size_diff = new_size - baseline_size
        if baseline_status != new_status:
            status_code_changes += 1
        if baseline_size != new_size:
            size_changes += 1
        url_key = str(item.get("url", "")).strip()
        if url_key not in by_url_counter:
            by_url_counter[url_key] = {
                "url": url_key,
                "count": 0,
                "status_change_count": 0,
                "max_abs_size_difference": 0,
                "baseline_response_codes": set(),
                "anomaly_response_codes": set(),
            }
        by_url_counter[url_key]["count"] += 1
        if baseline_status != new_status:
            by_url_counter[url_key]["status_change_count"] += 1
        by_url_counter[url_key]["max_abs_size_difference"] = max(
            _safe_int(by_url_counter[url_key]["max_abs_size_difference"], 0), abs(size_diff)
        )
        by_url_counter[url_key]["baseline_response_codes"].add(baseline_status)
        by_url_counter[url_key]["anomaly_response_codes"].add(new_status)
    for entry in by_url_counter.values():
        entry["baseline_response_codes"] = sorted(int(code) for code in entry["baseline_response_codes"])
        entry["anomaly_response_codes"] = sorted(int(code) for code in entry["anomaly_response_codes"])
    by_url = sorted(
        by_url_counter.values(),
        key=lambda item: (-_safe_int(item.get("count", 0)), str(item.get("url", ""))),
    )
    anomaly_summary_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "root_domain": root_domain,
        "input_parameters_file": str(parameters_path),
        "interrupted": interrupted,
        "interrupted_group": interrupted_group,
        "totals": {
            "total_results": len(result_entries),
            "total_anomalies": len(anomaly_entries),
            "total_reflections": len(reflection_entries),
            "unique_discrepancy_urls_count": len(unique_urls),
            "status_code_changes": status_code_changes,
            "size_changes": size_changes,
        },
        "unique_discrepancy_urls": unique_urls,
        "by_url": by_url,
        "discrepancies": result_entries,
        "results_folder_files": [],
    }
    anomaly_summary_path = results_dir / f"{root_domain}.results_summary.json"
    anomaly_summary_path.write_text(json.dumps(anomaly_summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    anomaly_summary_html_path = results_dir / f"{root_domain}.results_summary.html"
    anomaly_summary_html_path.write_text(render_anomaly_summary_html(anomaly_summary_payload), encoding="utf-8")

    anomaly_summary_payload["results_folder_files"] = list_results_folder_files(results_dir)
    anomaly_summary_path.write_text(json.dumps(anomaly_summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    anomaly_summary_html_path.write_text(render_anomaly_summary_html(anomaly_summary_payload), encoding="utf-8")
    return anomaly_summary_path, anomaly_summary_html_path


def render_anomaly_summary_html(payload: dict[str, Any]) -> str:
    rows = payload.get("discrepancies", []) if isinstance(payload.get("discrepancies"), list) else []

    def clip_cell(value: Any) -> tuple[str, str]:
        raw = str(value or "")
        return (
            f"<span class='clip-cell' title='{html.escape(raw)}'>{html.escape(raw)}</span>",
            raw,
        )

    def file_href(path_value: Any) -> str:
        raw = str(path_value or "").strip()
        if not raw:
            return ""
        normalized = raw.replace("\\", "/")
        return f"file:///{urllib.parse.quote(normalized, safe='/:._-()')}"

    def size_bytes_text(size_bytes: Any) -> str:
        return str(_safe_int(size_bytes, 0))

    def format_display_time(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            est = timezone(timedelta(hours=-5), name="EST")
            est_dt = dt.astimezone(est)
            hhmm_24 = est_dt.strftime("%H:%M")
            hhmm_12 = est_dt.strftime("%I:%M").lstrip("0") or "0:00"
            period = "morning" if est_dt.hour < 12 else "evening"
            return f"{est_dt:%m/%d/%Y} - {hhmm_24} EST - {hhmm_12} in the {period}"
        except Exception:
            return raw

    response_records: list[dict[str, Any]] = []
    detail_rows_html: list[str] = []
    for idx, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        response_id = f"r{idx + 1}"
        baseline_response = item.get("baseline_response") if isinstance(item.get("baseline_response"), dict) else {}
        anomaly_response = item.get("anomaly_response") if isinstance(item.get("anomaly_response"), dict) else {}
        response_diff_text = str(item.get("response_diff_text", "") or "")
        response_records.append(
            {
                "id": response_id,
                "baseline": baseline_response,
                "anomaly": anomaly_response,
                "diff": response_diff_text,
            }
        )

        captured_raw_value = str(item.get("captured_at_utc", "") or "")
        captured_display, captured_raw = clip_cell(format_display_time(captured_raw_value))
        url_display, url_raw = clip_cell(item.get("url", ""))
        param_display, param_raw = clip_cell(item.get("mutated_parameter", ""))
        value_display, value_raw = clip_cell(item.get("mutated_value", ""))
        result_raw = str(item.get("result_file", "") or "")
        result_link = (
            f"<a class='clip-cell file-link' title='{html.escape(result_raw)}' href='{html.escape(file_href(result_raw))}' "
            f"target='_blank' rel='noopener noreferrer'>{html.escape(result_raw)}</a>"
            if result_raw
            else ""
        )

        baseline_status = _safe_int(item.get("baseline_status", 0))
        anomaly_status = _safe_int(item.get("new_status", 0))
        baseline_size = _safe_int(item.get("baseline_size", 0))
        anomaly_size = _safe_int(item.get("new_size", 0))
        size_diff = anomaly_size - baseline_size

        baseline_label = f"{baseline_status} {_http_status_phrase(baseline_status)}"
        anomaly_label = f"{anomaly_status} {_http_status_phrase(anomaly_status)}"
        baseline_display, _ = clip_cell(baseline_label)
        anomaly_display, _ = clip_cell(anomaly_label)
        baseline_raw = str(baseline_status)
        anomaly_raw = str(anomaly_status)

        diff_class = "size-diff-zero"
        if size_diff > 0:
            diff_class = "size-diff-pos"
        elif size_diff < 0:
            diff_class = "size-diff-neg"
        diff_text = f"{size_diff:+d}"

        baseline_body = str(baseline_response.get("body_preview", "") or "")
        anomaly_body = str(anomaly_response.get("body_preview", "") or "")
        body_search = f"{baseline_body}\n{anomaly_body}".lower()

        detail_rows_html.append(
            f"<tr data-search-extra='{html.escape(body_search)}'>"
            f"<td data-raw='{html.escape(captured_raw)}'>{captured_display}</td>"
            f"<td data-raw='{html.escape(url_raw)}'>{url_display}</td>"
            f"<td data-raw='{html.escape(param_raw)}'>{param_display}</td>"
            f"<td data-raw='{html.escape(value_raw)}'>{value_display}</td>"
            f"<td data-raw='{html.escape(baseline_raw)}'><a href='#' class='resp-link' data-kind='baseline' data-id='{html.escape(response_id)}'>{baseline_display}</a></td>"
            f"<td data-raw='{html.escape(anomaly_raw)}'><a href='#' class='resp-link' data-kind='anomaly' data-id='{html.escape(response_id)}'>{anomaly_display}</a></td>"
            f"<td data-raw='{html.escape(size_bytes_text(baseline_size))}'>{html.escape(size_bytes_text(baseline_size))}</td>"
            f"<td data-raw='{html.escape(size_bytes_text(anomaly_size))}'>{html.escape(size_bytes_text(anomaly_size))}</td>"
            f"<td data-raw='{html.escape(diff_text)}'><span class='{diff_class}'>{html.escape(diff_text)}</span></td>"
            f"<td data-raw='diff'><a href='#' class='tool-diff' data-id='{html.escape(response_id)}'>diff</a></td>"
            f"<td data-raw='{html.escape(result_raw)}'>{result_link}</td>"
            "</tr>"
        )
    detail_table = "\n".join(detail_rows_html) if detail_rows_html else "<tr><td colspan='11'>No discrepancies</td></tr>"
    response_data_json = json.dumps(response_records, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>results summary</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 20px; background: #f6f8fb; color: #1f2937; }}
    h2 {{ margin: 0 0 12px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d1d5db; margin-bottom: 18px; table-layout: fixed; }}
    th, td {{ text-align: left; border: 1px solid #d1d5db; padding: 6px 8px; font-size: 12px; vertical-align: top; }}
    th {{ background: #eef2f7; top: 0; cursor: pointer; min-width: 80px; max-width: 280px; position: sticky; }}
    tr.filters th {{ top: 29px; background: #f8fafc; cursor: default; }}
    tr.filters input {{ width: 100%; box-sizing: border-box; padding: 4px 6px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 11px; }}
    .clip-cell {{ display: block; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; user-select: text; }}
    .file-link {{ color: #1d4ed8; text-decoration: underline; }}
    th.resizable {{ position: sticky; }}
    .resize-handle {{ position: absolute; top: 0; right: -2px; width: 6px; height: 100%; cursor: col-resize; user-select: none; }}
    .scroll {{ max-height: 640px; overflow: auto; border: 1px solid #d1d5db; }}
    .count-note {{ font-size: 12px; color: #6b7280; margin-bottom: 8px; }}
    .size-diff-pos {{ color: #15803d; font-weight: 600; }}
    .size-diff-neg {{ color: #b91c1c; font-weight: 600; }}
    .size-diff-zero {{ color: #374151; }}
    .modal-backdrop {{ display: none; position: fixed; inset: 0; background: rgba(0, 0, 0, 0.45); z-index: 1000; }}
    .modal {{ display: none; position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%); width: min(1200px, 92vw); height: min(85vh, 900px); background: #fff; border: 1px solid #d1d5db; border-radius: 8px; z-index: 1001; box-shadow: 0 10px 30px rgba(0,0,0,0.25); }}
    .modal-header {{ display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; border-bottom: 1px solid #e5e7eb; }}
    .modal-title {{ font-size: 14px; font-weight: 600; }}
    .modal-close {{ border: 1px solid #cbd5e1; background: #fff; border-radius: 4px; padding: 4px 8px; cursor: pointer; }}
    .modal-body {{ padding: 10px; overflow: auto; height: calc(100% - 48px); }}
    pre {{ margin: 0; white-space: pre-wrap; word-break: break-word; font-family: Consolas, 'Courier New', monospace; font-size: 12px; }}
  </style>
</head>
<body>
  <h2>All discrepancies</h2>
  <div class="count-note" id="detailCount"></div>
  <div class="scroll">
    <table id="detailTable" class="data-table">
      <thead>
        <tr>
          <th data-type="string">Captured UTC</th>
          <th data-type="string">URL</th>
          <th data-type="string">Parameter</th>
          <th data-type="string">Value</th>
          <th data-type="number">Baseline response</th>
          <th data-type="number">Anomaly response</th>
          <th data-type="number">Baseline size (bytes)</th>
          <th data-type="number">Anomaly size (bytes)</th>
          <th data-type="number">Size diff (bytes)</th>
          <th data-type="string">Tools</th>
          <th data-type="string">Result file</th>
        </tr>
        <tr class="filters">
          <th><input data-col="0" type="text" placeholder="Filter time"></th>
          <th><input data-col="1" type="text" placeholder="Filter URL"></th>
          <th><input data-col="2" type="text" placeholder="Filter param"></th>
          <th><input data-col="3" type="text" placeholder="Filter value"></th>
          <th><input data-col="4" type="text" placeholder="Filter baseline (code or phrase)"></th>
          <th><input data-col="5" type="text" placeholder="Filter anomaly (code or phrase)"></th>
          <th><input data-col="6" type="text" placeholder="Filter baseline bytes"></th>
          <th><input data-col="7" type="text" placeholder="Filter anomaly bytes"></th>
          <th><input data-col="8" type="text" placeholder="Filter diff bytes"></th>
          <th><input data-col="9" type="text" placeholder="Filter tools"></th>
          <th><input data-col="10" type="text" placeholder="Filter file"></th>
        </tr>
      </thead>
      <tbody>{detail_table}</tbody>
    </table>
  </div>

  <div class="modal-backdrop" id="modalBackdrop"></div>
  <div class="modal" id="viewerModal">
    <div class="modal-header">
      <div class="modal-title" id="modalTitle">Viewer</div>
      <button class="modal-close" id="modalClose" type="button">Close</button>
    </div>
    <div class="modal-body"><pre id="modalContent"></pre></div>
  </div>

  <script>
    (function() {{
      const responseData = {response_data_json};
      const responseDataById = new Map(responseData.map((item) => [String(item.id), item]));
      const stateStorageKey = "fozzy-report-state:" + String(window.location.href.split("#")[0]);
      const stateNamePrefix = "fozzy-report-state:";

      function readPersistedState() {{
        try {{
          const raw = localStorage.getItem(stateStorageKey);
          if (raw) return JSON.parse(raw);
        }} catch {{
        }}
        try {{
          const name = String(window.name || "");
          if (name.startsWith(stateNamePrefix)) {{
            return JSON.parse(name.slice(stateNamePrefix.length));
          }}
        }} catch {{
        }}
        return {{}};
      }}

      function writePersistedState(nextState) {{
        const safeState = nextState && typeof nextState === "object" ? nextState : {{}};
        const encoded = JSON.stringify(safeState);
        try {{
          localStorage.setItem(stateStorageKey, encoded);
          return;
        }} catch {{
        }}
        try {{
          window.name = `${{stateNamePrefix}}${{encoded}}`;
        }} catch {{
        }}
      }}

      function tableRawRowText(row) {{
        const base = Array.from(row.cells).map((cell) => (cell.dataset.raw || cell.textContent || "").toLowerCase()).join(" ");
        const extra = String(row.dataset.searchExtra || "").toLowerCase();
        return `${{base}} ${{extra}}`;
      }}

      function matchesFilter(text, filterValue) {{
        const rawText = String(text || "").toLowerCase();
        const query = String(filterValue || "").trim().toLowerCase();
        if (!query) return true;
        const tokens = query.split(/\\s+/).filter(Boolean);
        for (const token of tokens) {{
          if (token.startsWith("!")) {{
            const neg = token.slice(1);
            if (neg && rawText.includes(neg)) return false;
          }} else if (!rawText.includes(token)) {{
            return false;
          }}
        }}
        return true;
      }}

      function setupTable(tableId, countId) {{
        const table = document.getElementById(tableId);
        const count = document.getElementById(countId);
        if (!table) return;
        const tbody = table.querySelector("tbody");
        const headers = Array.from(table.querySelectorAll("thead tr:first-child th"));
        const filterInputs = Array.from(table.querySelectorAll("thead tr.filters input"));
        let sortIndex = -1;
        let sortAsc = true;

        function saveState() {{
          const state = readPersistedState();
          state.sortIndex = sortIndex;
          state.sortAsc = sortAsc;
          state.filters = filterInputs.map((input) => String(input.value || ""));
          const scrollEl = table.closest(".scroll");
          state.scrollTop = scrollEl ? scrollEl.scrollTop : 0;
          writePersistedState(state);
        }}

        function apply() {{
          const columnFilters = {{}};
          filterInputs.forEach((input) => {{
            const idx = Number(input.dataset.col || "-1");
            if (idx >= 0) columnFilters[idx] = (input.value || "");
          }});

          const rows = Array.from(tbody.querySelectorAll("tr"));
          let visible = 0;
          rows.forEach((row) => {{
            let show = true;
            const rowText = tableRawRowText(row);
            if (show) {{
              for (const [idxStr, value] of Object.entries(columnFilters)) {{
                if (!value) continue;
                const idx = Number(idxStr);
                const cell = row.cells[idx];
                const cellRaw = ((cell && cell.dataset.raw) || (cell && cell.textContent) || "").toLowerCase();
                if (!matchesFilter(cellRaw + " " + rowText, value)) {{
                  show = false;
                  break;
                }}
              }}
            }}
            row.style.display = show ? "" : "none";
            if (show) visible += 1;
          }});
          if (count) count.textContent = `Visible rows: ${{visible}}`;
          saveState();
        }}

        function sortBy(index) {{
          const th = headers[index];
          const isNumber = (th.dataset.type || "string") === "number";
          if (sortIndex === index) sortAsc = !sortAsc; else {{ sortIndex = index; sortAsc = true; }}
          const rows = Array.from(tbody.querySelectorAll("tr"));
          rows.sort((a, b) => {{
            const av = (a.cells[index] && (a.cells[index].dataset.raw || a.cells[index].textContent) || "").trim();
            const bv = (b.cells[index] && (b.cells[index].dataset.raw || b.cells[index].textContent) || "").trim();
            let cmp = 0;
            if (isNumber) {{
              const an = Number(av);
              const bn = Number(bv);
              cmp = (isNaN(an) ? 0 : an) - (isNaN(bn) ? 0 : bn);
            }} else {{
              cmp = av.localeCompare(bv);
            }}
            return sortAsc ? cmp : -cmp;
          }});
          rows.forEach((row) => tbody.appendChild(row));
          apply();
          saveState();
        }}

        headers.forEach((header, index) => {{
          header.addEventListener("click", () => sortBy(index));
        }});
        filterInputs.forEach((input) => input.addEventListener("input", apply));
        window.addEventListener("beforeunload", saveState);
        window.addEventListener("pagehide", saveState);

        const restored = readPersistedState();
        if (Array.isArray(restored.filters)) {{
          filterInputs.forEach((input, index) => {{
            if (index < restored.filters.length) {{
              input.value = String(restored.filters[index] || "");
            }}
          }});
        }}
        if (typeof restored.sortIndex === "number" && restored.sortIndex >= 0 && restored.sortIndex < headers.length) {{
          sortIndex = restored.sortIndex;
          sortAsc = typeof restored.sortAsc === "boolean" ? restored.sortAsc : true;
          const rows = Array.from(tbody.querySelectorAll("tr"));
          rows.sort((a, b) => {{
            const av = (a.cells[sortIndex] && (a.cells[sortIndex].dataset.raw || a.cells[sortIndex].textContent) || "").trim();
            const bv = (b.cells[sortIndex] && (b.cells[sortIndex].dataset.raw || b.cells[sortIndex].textContent) || "").trim();
            const isNumber = (headers[sortIndex].dataset.type || "string") === "number";
            let cmp = 0;
            if (isNumber) {{
              const an = Number(av);
              const bn = Number(bv);
              cmp = (isNaN(an) ? 0 : an) - (isNaN(bn) ? 0 : bn);
            }} else {{
              cmp = av.localeCompare(bv);
            }}
            return sortAsc ? cmp : -cmp;
          }});
          rows.forEach((row) => tbody.appendChild(row));
        }}
        apply();
        const scrollEl = table.closest(".scroll");
        if (typeof restored.scrollTop === "number" && scrollEl) {{
          scrollEl.scrollTop = restored.scrollTop;
          scrollEl.addEventListener("scroll", saveState, {{ passive: true }});
        }}
      }}

      function enableResizableColumns(tableId) {{
        const table = document.getElementById(tableId);
        if (!table) return;
        const headers = Array.from(table.querySelectorAll("thead tr:first-child th"));
        const restored = readPersistedState();
        const restoredWidths = Array.isArray(restored.columnWidths) ? restored.columnWidths : [];
        headers.forEach((header, index) => {{
          const restoredWidth = Number(restoredWidths[index]);
          if (restoredWidth > 0) {{
            header.style.width = `${{restoredWidth}}px`;
            header.style.maxWidth = `${{restoredWidth}}px`;
            table.querySelectorAll(`tbody tr td:nth-child(${{index + 1}}) .clip-cell`).forEach((node) => {{
              node.style.maxWidth = `${{restoredWidth}}px`;
            }});
          }}
        }});

        function saveColumnWidths() {{
          const state = readPersistedState();
          state.columnWidths = headers.map((header) => {{
            const width = Number.parseFloat(header.style.width || "0");
            return Number.isFinite(width) ? width : 0;
          }});
          writePersistedState(state);
        }}

        headers.forEach((header, index) => {{
          header.classList.add("resizable");
          if (header.querySelector(".resize-handle")) return;
          const handle = document.createElement("span");
          handle.className = "resize-handle";
          header.appendChild(handle);
          let startX = 0;
          let startWidth = 0;
          const onMove = (event) => {{
            const width = Math.max(80, startWidth + (event.clientX - startX));
            header.style.width = `${{width}}px`;
            header.style.maxWidth = `${{width}}px`;
            table.querySelectorAll(`tbody tr td:nth-child(${{index + 1}}) .clip-cell`).forEach((node) => {{
              node.style.maxWidth = `${{width}}px`;
            }});
          }};
          const onUp = () => {{
            window.removeEventListener("mousemove", onMove);
            window.removeEventListener("mouseup", onUp);
            saveColumnWidths();
          }};
          handle.addEventListener("mousedown", (event) => {{
            event.preventDefault();
            event.stopPropagation();
            startX = event.clientX;
            startWidth = header.getBoundingClientRect().width;
            window.addEventListener("mousemove", onMove);
            window.addEventListener("mouseup", onUp);
          }});
        }});
      }}

      const modalBackdrop = document.getElementById("modalBackdrop");
      const viewerModal = document.getElementById("viewerModal");
      const modalTitle = document.getElementById("modalTitle");
      const modalContent = document.getElementById("modalContent");
      const modalClose = document.getElementById("modalClose");

      function openModal(title, content) {{
        modalTitle.textContent = title;
        modalContent.textContent = content;
        modalBackdrop.style.display = "block";
        viewerModal.style.display = "block";
      }}

      function closeModal() {{
        modalBackdrop.style.display = "none";
        viewerModal.style.display = "none";
      }}

      modalClose.addEventListener("click", closeModal);
      modalBackdrop.addEventListener("click", closeModal);

      document.addEventListener("click", (event) => {{
        const baselineLink = event.target.closest(".resp-link");
        if (baselineLink) {{
          event.preventDefault();
          const id = String(baselineLink.dataset.id || "");
          const kind = String(baselineLink.dataset.kind || "");
          const record = responseDataById.get(id);
          if (!record) return;
          if (kind === "baseline") {{
            openModal("Baseline response", JSON.stringify(record.baseline || {{}}, null, 2));
          }} else {{
            openModal("Anomaly response", JSON.stringify(record.anomaly || {{}}, null, 2));
          }}
          return;
        }}

        const diffLink = event.target.closest(".tool-diff");
        if (diffLink) {{
          event.preventDefault();
          const id = String(diffLink.dataset.id || "");
          const record = responseDataById.get(id);
          if (!record) return;
          const diffText = String(record.diff || "").trim() || "No textual diff available.";
          openModal("Baseline vs anomaly diff", diffText);
        }}
      }});

      setupTable("detailTable", "detailCount");
      enableResizableColumns("detailTable");
    }})();
  </script>
</body>
</html>"""

def fuzz_group(
    group: RouteGroup,
    permutations: list[tuple[str, ...]],
    timeout: float,
    delay: float,
    quick_fuzz_values: list[str],
    results_dir: Path,
    dry_run: bool,
    dry_run_plan_lines: list[dict[str, Any]] | None = None,
    progress_callback: Callable[[str], None] | None = None,
    live_report_callback: Callable[[], None] | None = None,
    resume_requested: dict[str, Any] | None = None,
    mark_requested_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    total_requests = estimate_group_request_count(
        permutations=permutations,
        quick_fuzz_values_count=len(quick_fuzz_values),
    )
    summary = {
        "host": group.host,
        "path": group.path,
        "permutation_count": len(permutations),
        "total_requests": total_requests,
        "baseline_requests": 0,
        "fuzz_requests": 0,
        "anomalies": 0,
        "reflections": 0,
        "skipped_requests": 0,
        "anomaly_entries": [],
        "reflection_entries": [],
    }
    issued_requests = 0
    last_progress_at = 0.0
    if progress_callback:
        progress_callback(
            f"[{group.host}{group.path}] execution started (planned_requests={total_requests})"
        )
    for permutation in permutations:
        if not permutation:
            continue
        baseline_values = {name: baseline_seed_value_for(group.params[name]) for name in permutation}
        baseline_url = build_url(group.scheme, group.host, group.path, baseline_values)
        baseline_request_key = make_request_key(
            phase="fuzz_baseline",
            host=group.host,
            path=group.path,
            permutation=permutation,
            mutated_parameter=None,
            mutated_value=None,
            url=baseline_url,
        )
        baseline_ref = {
            **empty_expected_response(),
            "source": "fuzz_baseline",
            "note": "Populate from the matching baseline request response when executing this plan.",
        }
        if dry_run:
            queue_dry_run_request(
                plan_lines=dry_run_plan_lines,
                phase="fuzz_baseline",
                url=baseline_url,
                timeout=timeout,
                host=group.host,
                path=group.path,
                permutation=permutation,
                mutated_parameter=None,
                mutated_value=None,
                expected_baseline_response=baseline_ref,
            )
            summary["baseline_requests"] += 1
            if live_report_callback:
                try:
                    live_report_callback()
                except Exception:
                    pass
            if progress_callback and (summary["baseline_requests"] + summary["fuzz_requests"]) % 100 == 0:
                progress_callback(
                    f"[{group.host}{group.path}] progress baseline={summary['baseline_requests']} "
                    f"fuzz={summary['fuzz_requests']} anomalies={summary['anomalies']}"
                )
        else:
            stored_baseline = resume_requested.get(baseline_request_key) if isinstance(resume_requested, dict) else None
            if isinstance(stored_baseline, dict) and isinstance(stored_baseline.get("response"), dict):
                baseline_response = stored_baseline.get("response", {})
                summary["skipped_requests"] += 1
            else:
                issued_requests += 1
                if progress_callback:
                    now = time.monotonic()
                    if issued_requests == 1 or now - last_progress_at >= 5.0:
                        progress_callback(
                            f"[{group.host}{group.path}] sending request {issued_requests}/{total_requests}"
                        )
                        last_progress_at = now
                baseline_response = request_url(baseline_url, timeout=timeout)
                if mark_requested_callback:
                    mark_requested_callback(
                        {
                            "request_key": baseline_request_key,
                            "phase": "fuzz_baseline",
                            "host": group.host,
                            "path": group.path,
                            "permutation": list(permutation),
                            "mutated_parameter": None,
                            "mutated_value": None,
                            "url": baseline_url,
                            "response": baseline_response,
                            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                time.sleep(delay)
            if live_report_callback:
                try:
                    live_report_callback()
                except Exception:
                    pass
            summary["baseline_requests"] += 1
            if progress_callback and (summary["baseline_requests"] + summary["fuzz_requests"]) % 100 == 0:
                progress_callback(
                    f"[{group.host}{group.path}] progress baseline={summary['baseline_requests']} "
                    f"fuzz={summary['fuzz_requests']} anomalies={summary['anomalies']}"
                )

        for name in permutation:
            for fuzz_value in quick_fuzz_values:
                trial_values = dict(baseline_values)
                trial_values[name] = fuzz_value
                trial_url = build_url(group.scheme, group.host, group.path, trial_values)
                trial_request_key = make_request_key(
                    phase="fuzz_mutation",
                    host=group.host,
                    path=group.path,
                    permutation=permutation,
                    mutated_parameter=name,
                    mutated_value=fuzz_value,
                    url=trial_url,
                )
                if dry_run:
                    queue_dry_run_request(
                        plan_lines=dry_run_plan_lines,
                        phase="fuzz_mutation",
                        url=trial_url,
                        timeout=timeout,
                        host=group.host,
                        path=group.path,
                        permutation=permutation,
                        mutated_parameter=name,
                        mutated_value=fuzz_value,
                        expected_baseline_response=baseline_ref,
                    )
                    summary["fuzz_requests"] += 1
                    if live_report_callback:
                        try:
                            live_report_callback()
                        except Exception:
                            pass
                    if progress_callback and (summary["baseline_requests"] + summary["fuzz_requests"]) % 100 == 0:
                        progress_callback(
                            f"[{group.host}{group.path}] progress baseline={summary['baseline_requests']} "
                            f"fuzz={summary['fuzz_requests']} anomalies={summary['anomalies']}"
                        )
                    continue
                stored_trial = resume_requested.get(trial_request_key) if isinstance(resume_requested, dict) else None
                if isinstance(stored_trial, dict) and isinstance(stored_trial.get("response"), dict):
                    trial_response = stored_trial.get("response", {})
                    summary["skipped_requests"] += 1
                else:
                    issued_requests += 1
                    if progress_callback:
                        now = time.monotonic()
                        if issued_requests == 1 or now - last_progress_at >= 5.0:
                            progress_callback(
                                f"[{group.host}{group.path}] sending request {issued_requests}/{total_requests}"
                            )
                            last_progress_at = now
                    trial_response = request_url(trial_url, timeout=timeout)
                    if mark_requested_callback:
                        mark_requested_callback(
                            {
                                "request_key": trial_request_key,
                                "phase": "fuzz_mutation",
                                "host": group.host,
                                "path": group.path,
                                "permutation": list(permutation),
                                "mutated_parameter": name,
                                "mutated_value": fuzz_value,
                                "url": trial_url,
                                "response": trial_response,
                                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                    time.sleep(delay)
                summary["fuzz_requests"] += 1
                if live_report_callback:
                    try:
                        live_report_callback()
                    except Exception:
                        pass
                if progress_callback and (summary["baseline_requests"] + summary["fuzz_requests"]) % 100 == 0:
                    progress_callback(
                        f"[{group.host}{group.path}] progress baseline={summary['baseline_requests']} "
                        f"fuzz={summary['fuzz_requests']} anomalies={summary['anomalies']}"
                    )

                if response_differs(baseline_response, trial_response):
                    payload = {
                        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                        "host": group.host,
                        "path": group.path,
                        "permutation": list(permutation),
                        "mutated_parameter": name,
                        "mutated_value": fuzz_value,
                        "requested_url": trial_url,
                        "baseline": baseline_response,
                        "anomaly": trial_response,
                    }
                    anomaly_path = results_dir / anomaly_file_name(group.host, group.path, permutation, name, fuzz_value)
                    anomaly_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                    summary["anomalies"] += 1
                    summary["anomaly_entries"].append(
                        {
                            "result_type": "anomaly",
                            "result_file": str(anomaly_path),
                            "url": trial_url,
                            "host": group.host,
                            "path": group.path,
                            "mutated_parameter": name,
                            "mutated_value": fuzz_value,
                            "baseline_status": int(baseline_response.get("status", 0) or 0),
                            "new_status": int(trial_response.get("status", 0) or 0),
                            "baseline_size": int(baseline_response.get("size", 0) or 0),
                            "new_size": int(trial_response.get("size", 0) or 0),
                            "size_difference": int(trial_response.get("size", 0) or 0)
                            - int(baseline_response.get("size", 0) or 0),
                        }
                    )
                    if live_report_callback:
                        try:
                            live_report_callback()
                        except Exception:
                            pass
                body_preview = str(trial_response.get("body_preview", "") or "")
                if fuzz_value and body_preview and fuzz_value in body_preview:
                    reflection_payload = {
                        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                        "host": group.host,
                        "path": group.path,
                        "permutation": list(permutation),
                        "mutated_parameter": name,
                        "mutated_value": fuzz_value,
                        "requested_url": trial_url,
                        "reflection_detected": True,
                        "baseline": baseline_response,
                        "response": trial_response,
                    }
                    reflection_path = results_dir / reflection_file_name(
                        group.host, group.path, permutation, name, fuzz_value
                    )
                    reflection_path.write_text(json.dumps(reflection_payload, indent=2, ensure_ascii=False), encoding="utf-8")
                    summary["reflections"] += 1
                    summary["reflection_entries"].append(
                        {
                            "result_type": "reflection",
                            "result_file": str(reflection_path),
                            "url": trial_url,
                            "host": group.host,
                            "path": group.path,
                            "mutated_parameter": name,
                            "mutated_value": fuzz_value,
                            "baseline_status": int(baseline_response.get("status", 0) or 0),
                            "new_status": int(trial_response.get("status", 0) or 0),
                            "baseline_size": int(baseline_response.get("size", 0) or 0),
                            "new_size": int(trial_response.get("size", 0) or 0),
                            "size_difference": int(trial_response.get("size", 0) or 0)
                            - int(baseline_response.get("size", 0) or 0),
                        }
                    )
                    if live_report_callback:
                        try:
                            live_report_callback()
                        except Exception:
                            pass

    if progress_callback:
        progress_callback(
            f"[{group.host}{group.path}] execution finished "
            f"(baseline={summary['baseline_requests']} fuzz={summary['fuzz_requests']} "
            f"anomalies={summary['anomalies']} reflections={summary['reflections']} "
            f"skipped={summary['skipped_requests']})"
        )
    return summary


def main() -> None:
    args = parse_args()
    parameters_path = Path(args.parameters_file).resolve()
    if not parameters_path.exists():
        raise FileNotFoundError(f"Parameters file not found: {parameters_path}")

    payload = read_json(parameters_path)
    root_domain = str(payload.get("root_domain", parameters_path.stem)).strip().lower() or parameters_path.stem
    quick_fuzz_values = load_quick_fuzz_values(args.quick_fuzz_list)

    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    else:
        output_dir = (parameters_path.parent / "fozzy-output" / root_domain).resolve()
    results_dir = output_dir / "results"
    ensure_directory(output_dir)
    ensure_directory(results_dir)

    groups = load_route_groups(payload)
    if not groups:
        print("No parameterized routes were found in the input file.")
        return

    print(f"Loaded {len(groups)} host/path groups from: {parameters_path}")
    print(f"Output directory: {output_dir}")
    print("")

    host_listing: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for group in groups:
        host_listing[group.host].append((group.path, len(group.params)))

    print("Host -> path parameter counts:")
    for host in sorted(host_listing.keys()):
        print(f"- {host}")
        for path, count in sorted(host_listing[host], key=lambda item: item[0]):
            print(f"  {path} ({count} parameters)")
    print("")

    permutations_lines: list[str] = []
    baseline_permutation_urls: list[str] = []
    run_summary: list[dict[str, Any]] = []
    planned_summary: list[dict[str, Any]] = []
    route_inventory: list[dict[str, Any]] = []
    dry_run_plan_lines: list[dict[str, Any]] = []
    group_jobs: list[dict[str, Any]] = []
    interrupted = False
    interrupted_group: str | None = None
    cancelled_by_user = False

    for group in groups:
        try:
            required, optional = infer_required_optional_from_observed(group)
            generated_permutations = generate_permutations(
                observed_sets=group.observed_param_sets,
                required=set(group.params.keys()),
                optional=set(),
                max_permutations=int(args.max_permutations),
            )
            permutations = select_max_parameter_permutations(generated_permutations)

            estimated_requests = estimate_group_request_count(
                permutations=permutations,
                quick_fuzz_values_count=len(quick_fuzz_values),
            )
            print(
                f"[{group.host}{group.path}] observed_valid={len(group.observed_param_sets)} "
                f"required={len(required)} optional={len(optional)} "
                f"permutations_generated={len(generated_permutations)} permutations_used={len(permutations)} "
                f"estimated_requests={estimated_requests}"
            )

            for permutation in permutations:
                if not permutation:
                    continue
                params_meta = [group.params[name] for name in permutation]
                permutations_lines.append(build_placeholder_url(group.scheme, group.host, group.path, params_meta))
                baseline_values = {name: baseline_seed_value_for(group.params[name]) for name in permutation}
                baseline_permutation_urls.append(build_url(group.scheme, group.host, group.path, baseline_values))

            route_inventory.append(
                {
                    "host": group.host,
                    "path": group.path,
                    "scheme": group.scheme,
                    "parameter_count": len(group.params),
                    "parameters": [
                        {"name": name, "data_type": group.params[name].data_type}
                        for name in sorted(group.params.keys())
                    ],
                    "observed_valid_parameter_sets": [sorted(list(item)) for item in sorted(group.observed_param_sets)],
                    "inferred_required_parameters": sorted(required),
                    "inferred_optional_parameters": sorted(optional),
                    "permutations_generated_count": len(generated_permutations),
                    "permutations_used_count": len(permutations),
                    "permutations": [list(item) for item in permutations],
                    "placeholder_urls": [
                        build_placeholder_url(group.scheme, group.host, group.path, [group.params[name] for name in item])
                        for item in permutations
                        if item
                    ],
                    "baseline_urls": [
                        build_url(
                            group.scheme,
                            group.host,
                            group.path,
                            {name: baseline_seed_value_for(group.params[name]) for name in item},
                        )
                        for item in permutations
                        if item
                    ],
                }
            )
            group_jobs.append(
                {
                    "group": group,
                    "permutations": permutations,
                }
            )
        except KeyboardInterrupt:
            interrupted = True
            interrupted_group = f"{group.host}{group.path}"
            print(f"Interrupted by user during group: {interrupted_group}. Saving partial results...")
            break

    for job in group_jobs:
        planned_summary.append(
            fuzz_group(
                group=job["group"],
                permutations=job["permutations"],
                timeout=float(args.timeout),
                delay=float(args.delay),
                quick_fuzz_values=quick_fuzz_values,
                results_dir=results_dir,
                dry_run=True,
                dry_run_plan_lines=dry_run_plan_lines,
                progress_callback=None,
                live_report_callback=None,
            )
        )

    dry_run_plan_path = output_dir / f"{root_domain}.fozzy.requests.jsonl"
    with dry_run_plan_path.open("w", encoding="utf-8", newline="\n") as handle:
        for line in dry_run_plan_lines:
            handle.write(json.dumps(line, ensure_ascii=False))
            handle.write("\n")
    endpoint_counts = build_endpoint_request_counts(dry_run_plan_lines)
    endpoint_counts_json_path = output_dir / f"{root_domain}.fozzy.endpoint_request_counts.json"
    endpoint_counts_json_path.write_text(json.dumps(endpoint_counts, indent=2, ensure_ascii=False), encoding="utf-8")
    endpoint_counts_txt_path = output_dir / f"{root_domain}.fozzy.endpoint_request_counts.txt"
    endpoint_counts_txt_path.write_text(
        "\n".join(
            f"{item['request_count']}\t{item['endpoint']}"
            for item in endpoint_counts
        )
        + ("\n" if endpoint_counts else ""),
        encoding="utf-8",
    )

    planned_requests = len(dry_run_plan_lines)
    planned_delay = float(args.delay)
    estimated_runtime_seconds = planned_requests * max(0.0, planned_delay)
    print(
        f"Request plan ready: {dry_run_plan_path}\n"
        f"Planned requests: {planned_requests}\n"
        f"Configured delay: {planned_delay:.2f}s between requests\n"
        f"Estimated minimum runtime at that delay: {format_duration(estimated_runtime_seconds)}"
    )
    print("Requests by endpoint (most to least):")
    for item in endpoint_counts:
        print(f"  {item['request_count']:>6}  {item['endpoint']}")
    print(f"Endpoint request counts JSON: {endpoint_counts_json_path}")
    print(f"Endpoint request counts TXT: {endpoint_counts_txt_path}")

    selected_request_cap: int | None = None
    selected_group_jobs = list(group_jobs)
    selected_plan_lines = list(dry_run_plan_lines)
    selected_planned_requests = len(selected_plan_lines)
    selected_endpoint_count = len(endpoint_counts)

    if not args.dry_run and not interrupted and endpoint_counts:
        while True:
            try:
                cap_raw = input(
                    "Enter max requests per endpoint to include (blank for all): "
                ).strip()
            except EOFError:
                cap_raw = ""
            except KeyboardInterrupt:
                interrupted = True
                interrupted_group = interrupted_group or "selection_prompt"
                print("\nInterrupted during endpoint selection. Saving partial results...")
                cap_raw = ""
                break
            if cap_raw == "":
                break
            try:
                selected_request_cap = int(cap_raw)
            except ValueError:
                print("Please enter a whole number (or press Enter for all endpoints).")
                continue
            if selected_request_cap <= 0:
                print("Please enter a value greater than 0.")
                continue
            break

        if selected_request_cap is not None:
            allowed_endpoints = {
                str(item["endpoint"])
                for item in endpoint_counts
                if int(item["request_count"]) <= selected_request_cap
            }
            selected_group_jobs = [
                job
                for job in group_jobs
                if f"{job['group'].host}{job['group'].path}" in allowed_endpoints
            ]
            selected_plan_lines = [
                line
                for line in dry_run_plan_lines
                if f"{str(line.get('host', '')).strip().lower()}{str(line.get('path', '')).strip() or '/'}"
                in allowed_endpoints
            ]
            selected_planned_requests = len(selected_plan_lines)
            selected_endpoint_count = len(allowed_endpoints)
            selected_runtime_seconds = selected_planned_requests * max(0.0, planned_delay)
            print(
                f"Selected endpoints: {selected_endpoint_count}/{len(endpoint_counts)} "
                f"(max requests per endpoint <= {selected_request_cap})\n"
                f"Selected planned requests: {selected_planned_requests}\n"
                f"Estimated minimum runtime at that delay: {format_duration(selected_runtime_seconds)}"
            )

            if not selected_group_jobs:
                cancelled_by_user = True
                print("No endpoints matched the selected request cap. Execution cancelled.")

    resume_state_path = output_dir / f"{root_domain}.fozzy.resume_state.json"
    for line in selected_plan_lines:
        if "request_key" not in line:
            line["request_key"] = request_key_from_plan_line(line)

    resume_state = load_resume_state(resume_state_path)
    existing_requested = resume_state.get("requested", {}) if isinstance(resume_state.get("requested"), dict) else {}
    selected_keys = {str(line.get("request_key", "")) for line in selected_plan_lines if str(line.get("request_key", ""))}
    requested_for_selection: dict[str, Any] = {
        key: value for key, value in existing_requested.items() if key in selected_keys
    }

    queued_for_selection = [
        {
            "request_key": str(line.get("request_key", "")),
            "phase": str(line.get("phase", "")),
            "host": str(line.get("host", "")),
            "path": str(line.get("path", "")),
            "permutation": list(line.get("permutation", [])) if isinstance(line.get("permutation"), list) else [],
            "mutated_parameter": str(line.get("mutated_parameter", "") or ""),
            "mutated_value": str(line.get("mutated_value", "") or ""),
            "url": str(line.get("url", "")),
            "curl": str(line.get("curl", "")),
        }
        for line in selected_plan_lines
        if str(line.get("request_key", "")) and str(line.get("request_key", "")) not in requested_for_selection
    ]
    resume_state = {
        "requested": requested_for_selection,
        "queued": queued_for_selection,
        "meta": {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "root_domain": root_domain,
            "input_parameters_file": str(parameters_path),
            "selected_request_cap": selected_request_cap,
            "selected_planned_requests": len(selected_plan_lines),
            "requested_count": len(requested_for_selection),
            "queued_count": len(queued_for_selection),
        },
    }
    save_resume_state(resume_state_path, resume_state)
    if requested_for_selection:
        print(
            f"Resume state loaded: requested={len(requested_for_selection)} queued={len(queued_for_selection)} "
            f"file={resume_state_path}"
        )

    if not args.dry_run and not interrupted:
        if not cancelled_by_user:
            try:
                confirmation = input("Proceed with live execution? [y/N]: ").strip().lower()
            except EOFError:
                confirmation = ""
            except KeyboardInterrupt:
                interrupted = True
                interrupted_group = interrupted_group or "execution_prompt"
                print("\nInterrupted before execution confirmation. Saving partial results...")
                confirmation = "n"
            if confirmation not in {"y", "yes"}:
                cancelled_by_user = True
                print("Execution cancelled by user after request-plan review.")
            else:
                last_live_report_refresh = 0.0
                requested_cache = dict(resume_state.get("requested", {}))

                def maybe_refresh_live_report(force: bool = False) -> None:
                    nonlocal last_live_report_refresh
                    now = time.monotonic()
                    if not force and (now - last_live_report_refresh) < LIVE_REPORT_INTERVAL_SECONDS:
                        return
                    write_results_summary(
                        root_domain=root_domain,
                        parameters_path=parameters_path,
                        output_dir=output_dir,
                        results_dir=results_dir,
                        interrupted=interrupted,
                        interrupted_group=interrupted_group,
                    )
                    last_live_report_refresh = now

                def mark_request_completed(entry: dict[str, Any]) -> None:
                    key = str(entry.get("request_key", "")).strip()
                    if not key:
                        return
                    requested_cache[key] = entry
                    pending = [
                        {
                            "request_key": str(line.get("request_key", "")),
                            "phase": str(line.get("phase", "")),
                            "host": str(line.get("host", "")),
                            "path": str(line.get("path", "")),
                            "permutation": list(line.get("permutation", []))
                            if isinstance(line.get("permutation"), list)
                            else [],
                            "mutated_parameter": str(line.get("mutated_parameter", "") or ""),
                            "mutated_value": str(line.get("mutated_value", "") or ""),
                            "url": str(line.get("url", "")),
                            "curl": str(line.get("curl", "")),
                        }
                        for line in selected_plan_lines
                        if str(line.get("request_key", "")) and str(line.get("request_key", "")) not in requested_cache
                    ]
                    resume_payload = {
                        "requested": requested_cache,
                        "queued": pending,
                        "meta": {
                            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                            "root_domain": root_domain,
                            "input_parameters_file": str(parameters_path),
                            "selected_request_cap": selected_request_cap,
                            "selected_planned_requests": len(selected_plan_lines),
                            "requested_count": len(requested_cache),
                            "queued_count": len(pending),
                        },
                    }
                    save_resume_state(resume_state_path, resume_payload)

                maybe_refresh_live_report(force=True)
                for job in selected_group_jobs:
                    group = job["group"]
                    try:
                        group_summary = fuzz_group(
                            group=group,
                            permutations=job["permutations"],
                            timeout=float(args.timeout),
                            delay=float(args.delay),
                            quick_fuzz_values=quick_fuzz_values,
                            results_dir=results_dir,
                            dry_run=False,
                            dry_run_plan_lines=None,
                            progress_callback=print,
                            live_report_callback=maybe_refresh_live_report,
                            resume_requested=requested_cache,
                            mark_requested_callback=mark_request_completed,
                        )
                        run_summary.append(group_summary)
                    except KeyboardInterrupt:
                        interrupted = True
                        interrupted_group = f"{group.host}{group.path}"
                        print(f"Interrupted by user during group: {interrupted_group}. Saving partial results...")
                        maybe_refresh_live_report(force=True)
                        break

    if args.dry_run:
        run_summary = planned_summary

    previous_sigint_handler: Any = None
    sigint_ignored = False
    try:
        previous_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        sigint_ignored = True
    except Exception:
        previous_sigint_handler = None
        sigint_ignored = False

    permutations_txt_path = output_dir / f"{root_domain}.fozzy.permutations.txt"
    permutations_txt_path.write_text("\n".join(permutations_lines).strip() + ("\n" if permutations_lines else ""), encoding="utf-8")
    baseline_urls_txt_path = output_dir / f"{root_domain}.fozzy.baseline-urls.txt"
    baseline_urls_txt_path.write_text(
        "\n".join(baseline_permutation_urls).strip() + ("\n" if baseline_permutation_urls else ""),
        encoding="utf-8",
    )
    inventory_path = output_dir / f"{root_domain}.fozzy.inventory.json"
    inventory_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "root_domain": root_domain,
        "input_parameters_file": str(parameters_path),
        "routes": route_inventory,
    }
    inventory_path.write_text(json.dumps(inventory_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    summary_path = output_dir / f"{root_domain}.fozzy.summary.json"
    summary_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "root_domain": root_domain,
        "input_parameters_file": str(parameters_path),
        "dry_run": bool(args.dry_run),
        "interrupted": interrupted,
        "interrupted_group": interrupted_group,
        "cancelled_by_user": cancelled_by_user,
        "selected_request_cap": selected_request_cap,
        "selected_planned_requests": selected_planned_requests,
        "selected_endpoint_count": selected_endpoint_count,
        "groups": run_summary,
        "totals": {
            "groups": len(run_summary),
            "baseline_requests": sum(int(item.get("baseline_requests", 0)) for item in run_summary),
            "fuzz_requests": sum(int(item.get("fuzz_requests", 0)) for item in run_summary),
            "anomalies": sum(int(item.get("anomalies", 0)) for item in run_summary),
            "reflections": sum(int(item.get("reflections", 0)) for item in run_summary),
            "placeholder_permutations": len(permutations_lines),
            "planned_requests": len(dry_run_plan_lines),
        },
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    anomaly_summary_path, anomaly_summary_html_path = write_results_summary(
        root_domain=root_domain,
        parameters_path=parameters_path,
        output_dir=output_dir,
        results_dir=results_dir,
        interrupted=interrupted,
        interrupted_group=interrupted_group,
    )

    if sigint_ignored:
        try:
            signal.signal(signal.SIGINT, previous_sigint_handler)
        except Exception:
            pass
    print("")
    print(f"Permutations list: {permutations_txt_path}")
    print(f"Baseline URL list: {baseline_urls_txt_path}")
    print(f"Route inventory JSON: {inventory_path}")
    print(f"Run summary JSON: {summary_path}")
    print(f"Results summary JSON: {anomaly_summary_path}")
    print(f"Results summary HTML: {anomaly_summary_html_path}")
    print(f"Request plan JSONL: {dry_run_plan_path}")
    print(f"Results directory: {results_dir}")
    print(
        "Request totals: "
        f"baseline={summary_payload['totals']['baseline_requests']} "
        f"fuzz={summary_payload['totals']['fuzz_requests']} "
        f"anomalies={summary_payload['totals']['anomalies']} "
        f"reflections={summary_payload['totals']['reflections']}"
    )
    if interrupted:
        print("Run completed with interrupt; outputs contain partial results.")


if __name__ == "__main__":
    main()


