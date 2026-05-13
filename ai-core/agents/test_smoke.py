"""Non-interactive smoke test for the agent. Runs three canonical Track-4
test cases against the live ES index and prints structured results.

Usage:
    cd ai-core
    python -m agents.test_smoke
    python -m agents.test_smoke --case 1        # run just case 1
    python -m agents.test_smoke --quick         # 1 case only (fastest)

Requires the same .env as the FastAPI server (LLM_API_KEY, ES_URL, etc.).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

# Auto-load .env from common locations so users don't need to source it
try:
    from dotenv import load_dotenv
    for path in [Path.cwd() / ".env",
                 Path(__file__).parent.parent / ".env",
                 Path(__file__).parent / ".env"]:
        if path.exists():
            load_dotenv(path, override=False)
            break
except ImportError:
    pass

# Some langchain code paths still look for OPENAI_API_KEY. Mirror LLM_API_KEY.
if os.environ.get("LLM_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.environ["LLM_API_KEY"]

from agents.graph import build_agent, extract_final_report_or_raw  # noqa: E402
from agents.tools._es import close_es                                # noqa: E402


CASES = [
    {
        "id":   1,
        "name": "Шаринг контактов (Personal Data, неочевидный риск)",
        "feature": (
            "Добавляем новую кнопку для шаринга контактов пользователя "
            "(телефонная книга) с другими пользователями приложения. "
            "Пользователь заходит в раздел, контакты синхронизируются, "
            "и он может перевести деньги любому человеку из своей записной книжки."
        ),
        "expected_domains": ["personal_data"],
    },
    {
        "id":   2,
        "name": "Крупные P2P трансграничные переводы (AML/CFT)",
        "feature": (
            "Запускаем VIP-аккаунты для пользователей. Теперь они могут "
            "переводить до 50 000 USD одним платежом своим друзьям "
            "и родственникам за границу."
        ),
        "expected_domains": ["aml_cft", "payments"],
    },
    {
        "id":   3,
        "name": "Биометрия + хранение в plain text (Security + Personal Data)",
        "feature": (
            "Хотим заменить подтверждение переводов через SMS на биометрию. "
            "Пользователь будет прикладывать палец (TouchID) или сканировать "
            "лицо (FaceID) для подтверждения платежа. Сами сканы лиц мы "
            "планируем собирать и хранить на своих серверах в открытом виде "
            "для аналитики."
        ),
        "expected_domains": ["personal_data", "cybersecurity"],
    },
]


async def run_case(agent, case: dict) -> dict:
    started = time.perf_counter()
    session_id = str(uuid.uuid4())
    print()
    print("=" * 72)
    print(f"CASE {case['id']}: {case['name']}")
    print("-" * 72)
    print(f"feature: {case['feature']}")
    print()

    config = {"configurable": {"thread_id": session_id}}
    tool_calls: list[str] = []
    last_messages = []

    async for chunk in agent.astream(
        {"messages": [("user", case["feature"])]},
        config=config,
        stream_mode="values",
    ):
        msgs = chunk.get("messages") or []
        last_messages = msgs
        if not msgs:
            continue
        latest = msgs[-1]
        if hasattr(latest, "tool_calls") and latest.tool_calls:
            for call in latest.tool_calls:
                name = call.get("name", "?")
                tool_calls.append(name)
                args_preview = json.dumps(call.get("args") or {}, ensure_ascii=False)[:90]
                print(f"  → {name}({args_preview}{'...' if len(args_preview) >= 90 else ''})")

    extracted = extract_final_report_or_raw(last_messages)
    latency = round(time.perf_counter() - started, 2)
    print()
    print(f"  ⏱  {latency}s  ·  {len(tool_calls)} tool calls  ·  source={extracted.get('source')}")

    if extracted.get("report"):
        report = extracted["report"]
        domains_found = [d.get("domain") for d in report.get("domains") or []]
        overall = report.get("overall_risk", "?")
        red_flags = report.get("red_flags") or []
        print(f"  overall_risk = {overall}")
        print(f"  domains      = {domains_found}")
        if red_flags:
            print(f"  red_flags    = {red_flags[:3]}")
        # Hit-or-miss against expected
        expected = set(case["expected_domains"])
        found = set(domains_found or [])
        hit = expected & found
        miss = expected - found
        print(f"  expected     = {sorted(expected)}  hit={sorted(hit)}  miss={sorted(miss)}")
    else:
        print(f"  ⚠  No structured report. Source: {extracted.get('source')}")
        if extracted.get("raw"):
            print(f"     raw preview: {extracted['raw'][:200]}")
        elif extracted.get("error"):
            print(f"     error: {extracted['error']}")

    return {
        "case_id":     case["id"],
        "latency_s":   latency,
        "tool_calls":  tool_calls,
        "source":      extracted.get("source"),
        "report":      extracted.get("report"),
    }


async def main() -> None:
    ap = argparse.ArgumentParser(description="Agent smoke test against live ES")
    ap.add_argument("--case", type=int, default=None,
                    help="Run a specific case id (1..3)")
    ap.add_argument("--quick", action="store_true",
                    help="Run only the first case")
    args = ap.parse_args()

    agent = build_agent()
    print(f"agent built  ·  model={os.environ.get('AGENT_MODEL', 'openai/gpt-4o-mini')}")
    print(f"ES           ·  url={os.environ.get('ES_URL', 'http://localhost:9200')}  "
          f"index={os.environ.get('ES_INDEX', 'regtech-docs')}")

    cases = CASES
    if args.case is not None:
        cases = [c for c in CASES if c["id"] == args.case]
        if not cases:
            print(f"no such case: {args.case}")
            sys.exit(1)
    elif args.quick:
        cases = CASES[:1]

    summary = []
    try:
        for c in cases:
            summary.append(await run_case(agent, c))
    finally:
        await close_es()

    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    for r in summary:
        domains = ",".join(d.get("domain") for d in (r["report"] or {}).get("domains") or []) or "-"
        print(f"  case {r['case_id']}: {r['latency_s']:>5.2f}s  tools={len(r['tool_calls']):>2}  "
              f"source={r['source']}  domains={domains}")


if __name__ == "__main__":
    asyncio.run(main())
