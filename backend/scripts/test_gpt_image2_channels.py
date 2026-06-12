from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "gpt_image2_channel_tests"


PROMPTS = [
    "Premium ecommerce product photo of a white ceramic smart thermos bottle with subtle gold trim on a clean light gray studio surface, soft daylight, realistic shadow, no text, no watermark.",
    "High-end lifestyle product image of a white ceramic smart thermos bottle with gold trim beside a folded linen cloth, bright minimal scene, soft reflections, commercial photography, no text, no watermark.",
    "Modern catalog hero image of a white ceramic smart thermos bottle with gentle rim light, pale neutral background, crisp material texture, premium advertising style, no text, no logo, no watermark.",
]


def _load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _ensure_suffix(base: str, suffix: str) -> str:
    clean = (base or "").strip().rstrip("/")
    if not clean:
        return suffix
    if clean.endswith(suffix):
        return clean
    return f"{clean}{suffix}"


def _trim_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:6]}...{value[-4:]}"


def _extract_first_image(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if not isinstance(data, list):
        return ""
    for item in data:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if url:
            return url
        b64 = str(item.get("b64_json") or "").strip()
        if b64:
            return f"data:image/png;base64,{b64[:64]}..."
    return ""


def _extract_balance_value(payload: Any) -> Optional[float]:
    if not isinstance(payload, dict):
        return None
    if "left" in payload:
        try:
            return float(payload["left"])
        except Exception:
            return None
    data = payload.get("data")
    if isinstance(data, dict) and "quota" in data:
        try:
            return float(data["quota"])
        except Exception:
            return None
    return None


@dataclass
class AttemptResult:
    index: int
    prompt: str
    ok: bool
    elapsed_seconds: float
    status_code: Optional[int]
    image_url: str
    error: str
    response_excerpt: str


@dataclass
class ProviderResult:
    provider: str
    endpoint: str
    model: str
    balance_before: Optional[float]
    balance_after: Optional[float]
    balance_delta: Optional[float]
    attempts: List[AttemptResult]


def _provider_specs() -> List[Dict[str, Any]]:
    comfly_base = os.environ.get("COMFLY_API_BASE", "").strip()
    openmind_base = os.environ.get("OPENMIND_API_BASE", "").strip()
    yunwu_base = os.environ.get("YUNWU_API_BASE", "").strip()
    return [
        {
            "name": "comfly",
            "enabled": bool(comfly_base and os.environ.get("COMFLY_API_KEY")),
            "endpoint": _ensure_suffix(comfly_base, "/images/generations"),
            "model": "gpt-image-2",
            "headers": lambda: {
                "Authorization": f"Bearer {os.environ['COMFLY_API_KEY'].strip()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            "balance_fetcher": _fetch_comfly_balance,
        },
        {
            "name": "openmind",
            "enabled": bool(openmind_base and os.environ.get("OPENMIND_API_KEY")),
            "endpoint": _ensure_suffix(openmind_base, "/v1/images/generations"),
            "model": os.environ.get("OPENMIND_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2",
            "headers": lambda: {
                "User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
                "Authorization": f"Bearer {os.environ['OPENMIND_API_KEY'].strip()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            "balance_fetcher": _fetch_openmind_balance,
        },
        {
            "name": "yunwu",
            "enabled": bool(yunwu_base and os.environ.get("YUNWU_API_KEY")),
            "endpoint": _ensure_suffix(yunwu_base, "/v1/images/generations"),
            "model": "gpt-image-2",
            "headers": lambda: {
                "Authorization": f"Bearer {os.environ['YUNWU_API_KEY'].strip()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            "balance_fetcher": _fetch_yunwu_balance,
        },
    ]


def _fetch_json(url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        response = client.get(url, headers=headers)
    response.raise_for_status()
    if not response.content:
        return {}
    try:
        return response.json()
    except Exception:
        return {"_raw_text": response.text}


def _fetch_comfly_balance() -> Dict[str, Any]:
    user_id = os.environ.get("COMFLY_ACCOUNT_USER_ID", "").strip()
    token = os.environ.get("COMFLY_ACCOUNT_TOKEN", "").strip()
    api_base = os.environ.get("COMFLY_ACCOUNT_API_BASE", "").strip() or os.environ.get("COMFLY_API_BASE", "").strip()
    root = api_base.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3].rstrip("/")
    if not (user_id and token and root):
        return {}
    return _fetch_json(
        f"{root}/api/user/self",
        {
            "Authorization": f"Bearer {token}",
            "New-API-User": user_id,
            "Accept": "application/json",
        },
    )


def _fetch_yunwu_balance() -> Dict[str, Any]:
    user_id = os.environ.get("YUNWU_ACCOUNT_USER_ID", "").strip()
    token = os.environ.get("YUNWU_ACCOUNT_TOKEN", "").strip()
    api_base = os.environ.get("YUNWU_API_BASE", "").strip() or "https://yunwu.ai"
    root = api_base.rstrip("/")
    if not (user_id and token and root):
        return {}
    return _fetch_json(
        f"{root}/api/user/self",
        {
            "Authorization": token,
            "new-api-user": user_id,
            "Accept": "application/json",
        },
    )


def _fetch_openmind_balance() -> Dict[str, Any]:
    user_id = os.environ.get("OPENMIND_ACCOUNT_USER_ID", "").strip()
    token = os.environ.get("OPENMIND_ACCOUNT_TOKEN", "").strip()
    root = (os.environ.get("OPENMIND_ACCOUNT_API_BASE", "").strip() or os.environ.get("OPENMIND_API_BASE", "").strip()).rstrip("/")
    if not (user_id and token and root):
        return {}
    return _fetch_json(
        f"{root}/api/user/self",
        {
            "Authorization": token,
            "new-api-user": user_id,
            "Accept": "application/json",
        },
    )


def _normalize_ratio(value: str) -> str:
    ratio = str(value or "").strip()
    allowed = {"1:1", "3:2", "2:3", "16:9", "9:16"}
    return ratio if ratio in allowed else "1:1"


def _size_for_ratio(ratio: str) -> str:
    mapping = {
        "1:1": "1024x1024",
        "3:2": "1536x1024",
        "16:9": "1536x1024",
        "2:3": "1024x1536",
        "9:16": "1024x1536",
    }
    return mapping.get(_normalize_ratio(ratio), "1024x1024")


def _build_payload(model: str, prompt: str, ratio: str) -> Dict[str, Any]:
    normalized_ratio = _normalize_ratio(ratio)
    payload = {
        "model": model,
        "prompt": prompt,
        "size": _size_for_ratio(normalized_ratio),
        "n": 1,
        "response_format": "url",
    }
    if "gpt-image-2" in model or "gpt-image2" in model or "gptimage2" in model:
        payload["aspect_ratio"] = normalized_ratio
        payload["image_size"] = normalized_ratio
    return payload


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> httpx.Response:
    with httpx.Client(timeout=180.0, follow_redirects=True) as client:
        return client.post(url, headers=headers, json=payload)


def _run_provider(
    *,
    spec: Dict[str, Any],
    prompts: List[str],
    ratio: str,
) -> ProviderResult:
    balance_before_payload = spec["balance_fetcher"]()
    balance_before = _extract_balance_value(balance_before_payload)
    attempts: List[AttemptResult] = []
    for index, prompt in enumerate(prompts, start=1):
        payload = _build_payload(spec["model"], prompt, ratio)
        start = time.perf_counter()
        ok = False
        status_code: Optional[int] = None
        image_url = ""
        error = ""
        excerpt = ""
        try:
            response = _post_json(spec["endpoint"], spec["headers"](), payload)
            status_code = response.status_code
            excerpt = (response.text or "")[:600]
            response.raise_for_status()
            parsed = response.json() if response.content else {}
            image_url = _extract_first_image(parsed)
            ok = bool(image_url)
            if not ok:
                error = "No image URL returned"
        except Exception as exc:
            error = str(exc)
        elapsed_seconds = time.perf_counter() - start
        attempts.append(
            AttemptResult(
                index=index,
                prompt=prompt,
                ok=ok,
                elapsed_seconds=elapsed_seconds,
                status_code=status_code,
                image_url=image_url,
                error=error,
                response_excerpt=excerpt,
            )
        )
    balance_after_payload = spec["balance_fetcher"]()
    balance_after = _extract_balance_value(balance_after_payload)
    balance_delta = None
    if balance_before is not None and balance_after is not None:
        balance_delta = balance_after - balance_before
    return ProviderResult(
        provider=spec["name"],
        endpoint=spec["endpoint"],
        model=spec["model"],
        balance_before=balance_before,
        balance_after=balance_after,
        balance_delta=balance_delta,
        attempts=attempts,
    )


def _write_report(
    output_dir: Path,
    results: List[ProviderResult],
    prompts: List[str],
    ratio: str,
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"gpt_image2_channels_{stamp}.json"
    md_path = output_dir / f"gpt_image2_channels_{stamp}.md"

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ratio": ratio,
        "size": _size_for_ratio(ratio),
        "prompts": prompts,
        "providers": [
            {
                **asdict(provider),
                "attempts": [asdict(attempt) for attempt in provider.attempts],
            }
            for provider in results
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# gpt-image-2 Channel Test Report",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Aspect ratio: `{payload['ratio']}`",
        f"- Image size: `{payload['size']}`",
        "",
        "## Prompts",
        "",
    ]
    for idx, prompt in enumerate(prompts, start=1):
        lines.append(f"{idx}. {prompt}")
    lines.append("")
    lines.append("## Providers")
    lines.append("")
    for provider in results:
        lines.append(f"### {provider.provider}")
        lines.append("")
        lines.append(f"- Endpoint: `{provider.endpoint}`")
        lines.append(f"- Model: `{provider.model}`")
        lines.append(f"- Balance before: `{provider.balance_before}`")
        lines.append(f"- Balance after: `{provider.balance_after}`")
        lines.append(f"- Balance delta: `{provider.balance_delta}`")
        lines.append("")
        for attempt in provider.attempts:
            lines.append(
                f"- Attempt {attempt.index}: ok=`{attempt.ok}` status=`{attempt.status_code}` elapsed=`{attempt.elapsed_seconds:.2f}s`"
            )
            if attempt.image_url:
                lines.append(f"  - image: `{attempt.image_url}`")
            if attempt.error:
                lines.append(f"  - error: `{attempt.error}`")
        lines.append("")
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {"json": json_path, "md": md_path}


def main() -> int:
    parser = argparse.ArgumentParser(description="Test gpt-image-2 across local provider channels.")
    parser.add_argument("--ratio", default="9:16", help="Aspect ratio. Default: 9:16")
    parser.add_argument("--providers", default="comfly,openmind,yunwu", help="Comma-separated provider names")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for JSON/Markdown reports")
    args = parser.parse_args()

    _load_dotenv(ROOT / ".env")

    selected_names = {item.strip().lower() for item in args.providers.split(",") if item.strip()}
    specs = [spec for spec in _provider_specs() if spec["name"] in selected_names]
    if not specs:
        print("No providers selected.", file=sys.stderr)
        return 2

    enabled_specs = [spec for spec in specs if spec["enabled"]]
    disabled_specs = [spec["name"] for spec in specs if not spec["enabled"]]
    if disabled_specs:
        print(f"Skipped providers missing env config: {', '.join(disabled_specs)}")
    if not enabled_specs:
        print("No enabled providers to test.", file=sys.stderr)
        return 3

    print("Testing providers:")
    for spec in enabled_specs:
        print(f"- {spec['name']}: endpoint={spec['endpoint']} model={spec['model']}")

    results = []
    for spec in enabled_specs:
        print(f"\n=== Running {spec['name']} ===")
        result = _run_provider(spec=spec, prompts=PROMPTS, ratio=args.ratio)
        results.append(result)
        for attempt in result.attempts:
            marker = "OK" if attempt.ok else "FAIL"
            print(
                f"[{spec['name']}] attempt={attempt.index} {marker} "
                f"status={attempt.status_code} elapsed={attempt.elapsed_seconds:.2f}s"
            )
            if attempt.image_url:
                print(f"  image={attempt.image_url}")
            if attempt.error:
                print(f"  error={attempt.error}")
        print(
            f"[{spec['name']}] balance before={result.balance_before} "
            f"after={result.balance_after} delta={result.balance_delta}"
        )

    report_paths = _write_report(Path(args.output_dir), results, PROMPTS, args.ratio)
    print("\nReports written:")
    print(report_paths["json"])
    print(report_paths["md"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
