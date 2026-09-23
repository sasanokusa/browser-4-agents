import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))

import run as eval_run  # noqa: E402


def test_benchmark_has_50_bilingual_tasks_and_all_categories():
    tasks = eval_run.load_jsonl(ROOT / "eval" / "tasks.jsonl")
    assert len(tasks) == 50
    assert {task["lang"] for task in tasks} == {"ja", "en"}
    assert {task["category"] for task in tasks} == {"factual", "current", "comparison"}
    assert all(task["check"]["type"] in {"regex", "any"} for task in tasks)


def test_checks_regex_and_any_case_insensitively():
    assert eval_run.passes_check("The answer is OTTAWA.", {"type": "any", "value": ["Ottawa"]})
    assert eval_run.passes_check(
        "HTTP 404 not found; HTTP 403 forbidden", {"type": "regex", "value": r"404.*403"}
    )
    assert not eval_run.passes_check("Toronto", {"type": "any", "value": ["Ottawa"]})


def test_fake_openai_and_browsr_tool_round_trip_keeps_argument_string_unparsed():
    raw_arguments = '{ "query" : "latest Python release", "extra": [1, 2] }'
    seen_calls = []
    model_requests = []
    completions = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal completions
        if request.url.path == "/tools":
            assert request.url.params.get("mode") is None
            return httpx.Response(
                200,
                json=[
                    {"type": "function", "function": {"name": "search", "parameters": {}}},
                    {"type": "function", "function": {"name": "open", "parameters": {}}},
                ],
            )
        if request.url.path == "/call":
            body = json.loads(request.content)
            seen_calls.append((body, request.headers.get("x-session")))
            return httpx.Response(200, json={"query": "latest Python release", "results": []})
        if request.url.path == "/v1/chat/completions":
            model_requests.append(json.loads(request.content))
            completions += 1
            if completions == 1:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call-1",
                                            "type": "function",
                                            "function": {
                                                "name": "search",
                                                "arguments": raw_arguments,
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Python's latest stable series is Python 3.14.",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 16, "completion_tokens": 9, "total_tokens": 25},
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://fake"
        ) as client:
            tools = await eval_run.request_tools(client, "http://fake", "standard")
            task = {
                "id": "test-1",
                "question": "Latest Python release?",
                "lang": "en",
                "category": "current",
                "check": {"type": "regex", "value": r"Python 3\.14"},
            }
            result = await eval_run.run_task(
                client,
                task=task,
                tools=tools,
                llm_url="http://fake/v1/chat/completions",
                model="fake-model",
                browsr_url="http://fake",
                max_steps=8,
                run_id="run1",
                max_completion_tokens=128,
                reasoning_effort="low",
                llm_params={"think": False},
            )
            assert result["success"] is True
            assert result["usage"] == {
                "prompt_tokens": 28,
                "completion_tokens": 12,
                "total_tokens": 40,
            }
            assert result["usage_response_count"] == 2
            assert result["tool_call_count"] == 1
            assert seen_calls == [
                ({"name": "search", "arguments": raw_arguments}, "eval-run1-test-1")
            ]
            assert all(request["max_tokens"] == 128 for request in model_requests)
            assert all(request["reasoning_effort"] == "low" for request in model_requests)
            assert all(request["think"] is False for request in model_requests)
            records = [
                {
                    "session": result["session"],
                    "tool": "search",
                    "action": "search",
                    "corrected": False,
                    "outcome": "ok",
                    "ms": 7,
                    "out_tokens": 4,
                }
            ]
            report = eval_run.summarize([result], records)
            assert report["metrics"]["success_rate"] == 1.0
            assert report["metrics"]["tool_selection_accuracy"] == 1.0
            assert report["metrics"]["latency_ms_p50"] == 7
            assert report["metrics"]["total_tokens_per_task"] == 40
            assert report["metrics"]["mean_completion_tokens_per_response"] == 6
            assert report["metrics"]["mean_out_tokens_per_tool_response"] == 4
            assert report["tasks"][0]["call_metrics"]["uncorrected_rate"] == 1.0
            assert report["tasks"][0]["call_metrics"]["call_log_complete"] is True

    asyncio.run(exercise())


def test_variant_matrix_covers_design_comparison_axes():
    paths = list((ROOT / "eval" / "variants").glob("*-*-*-*.toml"))
    variants = [eval_run.load_variant(path) for path in paths]
    assert {v["mode"] for v in variants} == {"standard", "single"}
    assert {v["max_tokens"] for v in variants} == {1000, 3000, 6000}
    assert {v["link_style"] for v in variants} == {"id", "url"}
    assert len(paths) >= 36


def test_missing_usage_and_call_log_metrics_are_not_fabricated():
    result = {
        "id": "without-usage",
        "session": "missing",
        "success": False,
        "usage": None,
        "usage_response_count": 0,
    }
    metrics = eval_run.summarize([result], [])["metrics"]
    assert metrics["total_tokens"] is None
    assert metrics["total_tokens_per_task"] is None
    assert metrics["mean_out_tokens_per_tool_response"] is None


def test_truncated_completion_cannot_pass_the_task():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Ottawa"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            },
        )

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://fake"
        ) as client:
            task = {
                "id": "truncated",
                "question": "Capital of Canada?",
                "lang": "en",
                "category": "factual",
                "check": {"type": "any", "value": ["Ottawa"]},
            }
            result = await eval_run.run_task(
                client,
                task=task,
                tools=[],
                llm_url="http://fake/v1/chat/completions",
                model="fake",
                browsr_url="http://fake",
                max_steps=1,
                run_id="run2",
            )
            assert result["checker_match"] is True
            assert result["incomplete"] is True
            assert result["success"] is False

    asyncio.run(exercise())
