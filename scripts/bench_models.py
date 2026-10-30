"""Compare candidate Nemotron models on one real email.

The README claims a specific model choice for a specific reason. This script is
how that claim was measured: same email, same prompt, same temperature, one row
per model.

    python scripts/bench_models.py --index 0
    python scripts/bench_models.py --models nvidia/nemotron-3-super-120b-a12b,nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B

Nothing is written to the database and no result is cached, so repeated runs
measure the model rather than the cache.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.config import get_settings  # noqa: E402
from backend.errors import AppError  # noqa: E402
from backend.services import analyzer, gmail_client, nebius_client  # noqa: E402

DEFAULT_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "nvidia/Nemotron-3_5-Lightning",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, default=0, help="which email to use")
    parser.add_argument("--message-id", default=None)
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="attempts per model; >1 turns a lucky run into a reliability number",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not nebius_client.is_configured():
        print("NEBIUS_API_KEY is not configured; nothing to measure.")
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.message_id:
        email = gmail_client.get_email(args.message_id)
    else:
        listed = gmail_client.list_emails(max_results=max(args.index + 1, 1))
        if args.index >= len(listed):
            print(f"Only {len(listed)} emails available.")
            return 2
        email = gmail_client.get_email(listed[args.index].id)

    body_chars = len(email.body_text or "")
    print(f"email   : {(email.subject or '(no subject)')[:70]}")
    print(f"body    : {body_chars} chars")
    print(f"prompt  : {len(analyzer.SYSTEM_PROMPT)} char system prompt")
    print()

    header = (
        f"{'model':44s} {'ok':>4s} {'latency':>9s} {'prompt':>7s} "
        f"{'compl':>7s} {'facts':>6s} {'rej':>4s}"
    )
    print(header)
    print("-" * len(header))

    rows: list[tuple[str, int, int, int, int, int]] = []
    for model in models:
        attempts = 0
        for _ in range(max(args.repeat, 1)):
            attempts += 1
            try:
                analysis, usage, audit = analyzer.analyse(email, model=model)
            except AppError as exc:
                print(f"{model:44s} {'-':>4s}  FAILED  {exc.code}: {exc.message[:44]}")
                continue
            rejected = len(audit.rejected)
            rows.append(
                (
                    model,
                    usage.latency_ms or 0,
                    usage.prompt_tokens or 0,
                    usage.completion_tokens or 0,
                    len(analysis.key_facts),
                    rejected,
                )
            )
            print(
                f"{model:44s} {'yes':>4s} {usage.latency_ms or 0:8d}ms "
                f"{usage.prompt_tokens or 0:7d} {usage.completion_tokens or 0:7d} "
                f"{len(analysis.key_facts):6d} {rejected:4d}"
            )
        if attempts > 1:
            passed = len([r for r in rows if r[0] == model])
            print(f"{'':44s} {'':>4s}  -> {passed}/{attempts} attempts produced valid JSON")

    if len(rows) >= 2:
        print()
        fastest = min(rows, key=lambda r: r[1])
        baseline = rows[0]
        print(
            f"fastest: {fastest[0]}  ({fastest[1]}ms vs {baseline[1]}ms baseline, "
            f"{baseline[1] / max(fastest[1], 1):.1f}x)"
        )
    print()
    print(f"configured fast      : {settings.nebius_model_fast}")
    print(f"configured reasoning : {settings.nebius_model_reasoning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
