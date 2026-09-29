"""OpenAI compatibility assertions ported from opendatahub-tests.

Source: tests/model_serving/model_server/llmd/api_compat/openai.py,
commit 92a93ed7188391ec70f4c522d72ea7bbf244b4e2. Verification bodies retain
source semantics, including optional tool production and stop-sequence behavior.
The existing runner supplies endpoint, bearer token, and bounded request timeout;
Kubernetes resource ownership stays in Deployer.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import openai
from openai import DefaultHttpxClient, OpenAI
from openai.types import ResponseFormatJSONObject
from openai.types.chat import (
    ChatCompletion, ChatCompletionAssistantMessageParam, ChatCompletionMessageToolCallParam,
    ChatCompletionSystemMessageParam, ChatCompletionToolMessageParam, ChatCompletionToolParam,
    ChatCompletionUserMessageParam,
)

LOGGER = logging.getLogger(__name__)

COMPAT_TEST_TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather in a given location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City and state, e.g. San Francisco, CA",
                    },
                },
                "required": ["location"],
            },
        },
    }
]


@dataclass
class IterationFailure:
    iteration: int
    elapsed_seconds: float
    error: str


@dataclass
class CompatSuiteResult:
    passed: int = 0
    failed: int = 0
    failures: list[IterationFailure] = field(default_factory=list)


class OpenAICompatibilityValidator:
    """Run each source verification once, or repeatedly for a bounded duration."""

    def __init__(self, base_url: str, model_name: str, bearer_token: str = "", timeout: float = 120):
        self._base_url = base_url
        self._model = model_name
        self._client = OpenAI(
            base_url=f"{base_url.rstrip('/')}/v1",
            api_key=bearer_token or "unused",
            http_client=DefaultHttpxClient(verify=False),
            timeout=timeout,
            max_retries=0,
        )

    def _ensure_client(self) -> OpenAI:
        return self._client

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._client.close()

    def _collect_diagnostics(self) -> str:
        return f"Endpoint: {self._base_url}; model: {self._model}"

    VERIFICATIONS: tuple[str, ...] = (
        "verify_models_endpoint",
        "verify_chat_completion",
        "verify_chat_completion_usage",
        "verify_streaming",
        "verify_streaming_sse_integrity",
        "verify_system_prompt",
        "verify_multi_turn",
        "verify_json_mode",
        "verify_stop_sequences",
        "verify_sampling_params",
        "verify_max_tokens",
        "verify_logprobs",
        "verify_n_completions",
        "verify_seed",
        "verify_error_forwarding",
    )


    TOOL_CALLING_VERIFICATIONS: tuple[str, ...] = (
        "verify_tool_calling",
        "verify_tool_calling_streaming",
        "verify_parallel_tool_calls",
        "verify_multi_turn_tool_use",
    )


    ALL_VERIFICATIONS: tuple[str, ...] = VERIFICATIONS + TOOL_CALLING_VERIFICATIONS


    def _loop_for(self, verification: str, duration: int) -> CompatSuiteResult:
        """Run a single verification in a loop for a time period."""
        result = CompatSuiteResult()
        start = time.monotonic()
        deadline = start + duration
        LOGGER.info(f"Looping {verification} for {duration}s")

        while result.passed + result.failed == 0 or time.monotonic() < deadline:
            iteration = result.passed + result.failed + 1
            elapsed = time.monotonic() - start
            try:
                getattr(self, verification)()
                result.passed += 1
            except (AssertionError, openai.OpenAIError) as e:
                result.failed += 1
                result.failures.append(
                    IterationFailure(
                        iteration=iteration,
                        elapsed_seconds=round(elapsed, 1),
                        error=str(e),
                    )
                )
                LOGGER.error(f"{verification} iteration {iteration} FAILED at {elapsed:.0f}s: {e}")

        total = result.passed + result.failed
        LOGGER.info(f"{verification} loop done — {result.passed}/{total} passed over {duration}s")
        return result


    def _loop_for_or_raise(self, verification: str, duration: int) -> None:
        """Run a single verification in a loop, raising on any failure."""
        result = self._loop_for(verification=verification, duration=duration)
        if result.failures:
            failure_details = "\n".join(
                f"  iteration {f.iteration} ({f.elapsed_seconds}s): {f.error}" for f in result.failures
            )
            diagnostics = self._collect_diagnostics()
            first_error = result.failures[0].error
            raise AssertionError(
                f"{verification}: {result.failed}/{result.passed + result.failed} "
                f"iterations failed over {duration}s — {first_error}\n\n"
                f"Failures:\n{failure_details}\n\n{diagnostics}"
            )


    @contextmanager
    def _api_call(self, operation: str) -> Generator[None]:
        """Context manager that wraps SDK calls with diagnostics on failure."""
        try:
            yield
        except openai.APIConnectionError as e:
            diagnostics = self._collect_diagnostics()
            LOGGER.error(f"{operation} failed: connection error\n{diagnostics}")
            raise AssertionError(
                f"{operation} failed: cannot connect to {self._base_url}\n  Error: {e}\n\n{diagnostics}"
            ) from e
        except openai.AuthenticationError as e:
            diagnostics = self._collect_diagnostics()
            LOGGER.error(f"{operation} failed: authentication error (HTTP {e.status_code})\n{diagnostics}")
            raise AssertionError(
                f"{operation} failed: authentication error (HTTP {e.status_code})\n"
                f"  Response: {e.response.text if e.response else 'N/A'}\n"
                f"\n{diagnostics}"
            ) from e
        except openai.APIStatusError as e:
            diagnostics = self._collect_diagnostics()
            LOGGER.error(
                f"{operation} failed: HTTP {e.status_code}\n"
                f"  Response: {e.response.text if e.response else 'N/A'}\n"
                f"{diagnostics}"
            )
            raise AssertionError(
                f"{operation} failed: HTTP {e.status_code}\n"
                f"  Response: {e.response.text[:1000] if e.response else 'N/A'}\n"
                f"\n{diagnostics}"
            ) from e
        except openai.APIResponseValidationError as e:
            LOGGER.error(
                f"{operation} failed: response did not match OpenAI schema\n"
                f"  Validation error: {e}\n"
                f"  Raw body: {e.response.text[:1000] if e.response else 'N/A'}"
            )
            raise AssertionError(
                f"{operation} failed: server response does not conform to OpenAI API schema\n"
                f"  Validation error: {e}\n"
                f"  Raw body: {e.response.text[:1000] if e.response else 'N/A'}"
            ) from e


    def verify_models_endpoint(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_models_endpoint", duration=duration)
        LOGGER.info("Verifying /v1/models endpoint")
        with self._api_call("/v1/models"):
            response = self._ensure_client().models.list()
        models = list(response)

        assert len(models) > 0, f"/v1/models returned empty list — expected at least the deployed model '{self._model}'"
        for model in models:
            assert model.id, f"Model entry has empty id. Full entry: {model.model_dump()}"
            assert model.object == "model", (
                f"Expected object='model', got '{model.object}'. Full entry: {model.model_dump()}"
            )
            assert model.created is not None, (
                f"Model '{model.id}' has no 'created' field. Full entry: {model.model_dump()}"
            )

        LOGGER.info(f"/v1/models returned {len(models)} model(s): {[m.id for m in models]}")


    def verify_chat_completion(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_chat_completion", duration=duration)
        LOGGER.info("Verifying standard chat completion response shape")
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "Reply with exactly the word 'hello'."},
        ]
        with self._api_call("/v1/chat/completions"):
            response = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=10,
                temperature=0,
            )
        self._assert_chat_completion_shape(response=response)
        LOGGER.info(f"Chat completion OK — id={response.id}, content={response.choices[0].message.content!r}")


    def verify_chat_completion_usage(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_chat_completion_usage", duration=duration)
        LOGGER.info("Verifying chat completion usage statistics")
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "Say hi."},
        ]
        with self._api_call("/v1/chat/completions (usage)"):
            response = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=10,
                temperature=0,
            )
        self._assert_usage_stats(response=response)
        LOGGER.info(
            f"Usage stats OK — prompt_tokens={response.usage.prompt_tokens}, "
            f"completion_tokens={response.usage.completion_tokens}, "
            f"total_tokens={response.usage.total_tokens}"
        )


    def verify_streaming(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_streaming", duration=duration)
        LOGGER.info("Verifying streaming chat completion (SSE)")
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "Count from 1 to 3."},
        ]
        with self._api_call("/v1/chat/completions (stream)"):
            stream = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=30,
                temperature=0,
                stream=True,
            )
            chunks = list(stream)

        assert len(chunks) > 0, "No stream chunks received — server closed connection without sending data"

        content_parts: list[str] = []
        found_finish_reason = False

        for i, chunk in enumerate(chunks):
            assert chunk.object == "chat.completion.chunk", (
                f"Chunk #{i}: expected object='chat.completion.chunk', "
                f"got '{chunk.object}'. Full chunk: {chunk.model_dump()}"
            )
            assert chunk.model, f"Chunk #{i}: empty model field. Full chunk: {chunk.model_dump()}"
            assert len(chunk.choices) > 0, f"Chunk #{i}: empty choices. Full chunk: {chunk.model_dump()}"

            choice = chunk.choices[0]
            if choice.delta.content:
                content_parts.append(choice.delta.content)
            if choice.finish_reason is not None:
                found_finish_reason = True

        assembled_text = "".join(content_parts)
        assert len(assembled_text) > 0, (
            f"Assembled streaming content is empty — received {len(chunks)} chunks but none contained text content"
        )
        assert found_finish_reason, (
            f"No chunk contained a finish_reason — received {len(chunks)} chunks. Last chunk: {chunks[-1].model_dump()}"
        )
        LOGGER.info(f"Streaming OK — {len(chunks)} chunks, assembled text: {assembled_text!r}")


    def verify_streaming_sse_integrity(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_streaming_sse_integrity", duration=duration)
        LOGGER.info("Verifying streaming SSE integrity (ordering, IDs, finish_reason)")
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "Write a short paragraph about the ocean."},
        ]
        with self._api_call("/v1/chat/completions (SSE integrity)"):
            stream = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=80,
                temperature=0,
                stream=True,
            )
            chunks = list(stream)

        assert len(chunks) >= 2, f"Expected at least 2 stream chunks (content + finish), got {len(chunks)}"

        chunk_ids = {chunk.id for chunk in chunks}
        assert len(chunk_ids) == 1, (
            f"All chunks must share the same completion ID, got {len(chunk_ids)} distinct IDs: {chunk_ids}"
        )

        completion_id = chunks[0].id
        assert completion_id, "Completion ID is empty"

        model_names = {chunk.model for chunk in chunks}
        assert len(model_names) == 1, f"All chunks must report the same model, got: {model_names}"

        finish_reason_chunks = [
            i for i, chunk in enumerate(chunks) if chunk.choices and chunk.choices[0].finish_reason is not None
        ]
        assert len(finish_reason_chunks) == 1, (
            f"Expected exactly 1 chunk with finish_reason, got {len(finish_reason_chunks)} "
            f"at indices {finish_reason_chunks}"
        )
        assert finish_reason_chunks[0] >= len(chunks) - 2, (
            f"finish_reason chunk should be at or near the end (index {finish_reason_chunks[0]} "
            f"of {len(chunks)} chunks)"
        )

        finish_reason = chunks[finish_reason_chunks[0]].choices[0].finish_reason
        assert finish_reason in ("stop", "length"), (
            f"Unexpected finish_reason '{finish_reason}', expected 'stop' or 'length'"
        )

        for i, chunk in enumerate(chunks):
            assert chunk.object == "chat.completion.chunk", (
                f"Chunk #{i}: expected object='chat.completion.chunk', got '{chunk.object}'"
            )
            assert len(chunk.choices) > 0, f"Chunk #{i}: empty choices array"
            assert chunk.choices[0].index == 0, f"Chunk #{i}: expected choice index=0, got {chunk.choices[0].index}"

        assembled = "".join(content for chunk in chunks if (content := chunk.choices[0].delta.content))
        assert len(assembled) > 0, f"No text content assembled from {len(chunks)} chunks"

        LOGGER.info(
            f"SSE integrity OK — {len(chunks)} chunks, 1 completion ID ({completion_id}), "
            f"finish_reason='{finish_reason}', assembled {len(assembled)} chars"
        )


    def verify_system_prompt(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_system_prompt", duration=duration)
        LOGGER.info("Verifying system prompt handling")
        system_msg: ChatCompletionSystemMessageParam = {
            "role": "system",
            "content": "You are a helpful assistant.",
        }
        user_msg: ChatCompletionUserMessageParam = {"role": "user", "content": "Hello!"}
        with self._api_call("/v1/chat/completions (system prompt)"):
            response = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=[system_msg, user_msg],
                max_tokens=20,
                temperature=0,
            )
        self._assert_chat_completion_shape(response=response)
        LOGGER.info(f"System prompt OK — content={response.choices[0].message.content!r}")


    def verify_multi_turn(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_multi_turn", duration=duration)
        LOGGER.info("Verifying multi-turn conversation handling")
        turn1_user: ChatCompletionUserMessageParam = {
            "role": "user",
            "content": "My name is Alice.",
        }
        turn1_assistant: ChatCompletionAssistantMessageParam = {
            "role": "assistant",
            "content": "Nice to meet you, Alice!",
        }
        turn2_user: ChatCompletionUserMessageParam = {
            "role": "user",
            "content": "What is my name?",
        }
        with self._api_call("/v1/chat/completions (multi-turn)"):
            response = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=[turn1_user, turn1_assistant, turn2_user],
                max_tokens=30,
                temperature=0,
            )
        self._assert_chat_completion_shape(response=response)
        content = response.choices[0].message.content or ""
        assert "alice" in content.lower(), (
            f"Multi-turn context lost — expected 'Alice' in response, "
            f"got: {content!r}. Full response: {response.model_dump()}"
        )
        LOGGER.info(f"Multi-turn OK — content={content!r}")


    def verify_json_mode(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_json_mode", duration=duration)
        LOGGER.info("Verifying JSON mode (response_format)")
        messages: list[ChatCompletionUserMessageParam] = [
            {
                "role": "user",
                "content": "Return a JSON object with a single key 'color' and value 'blue'.",
            },
        ]
        with self._api_call("/v1/chat/completions (json_mode)"):
            result = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                response_format=ResponseFormatJSONObject(type="json_object"),
                max_tokens=50,
                temperature=0,
            )
        response: ChatCompletion = result  # type: ignore[assignment]
        self._assert_chat_completion_shape(response=response)
        content = response.choices[0].message.content or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            raise AssertionError(
                f"JSON mode response is not valid JSON.\n"
                f"  Raw content: {content!r}\n"
                f"  Full response: {response.model_dump()}"
            ) from e
        assert isinstance(parsed, dict), (
            f"JSON mode response is not a JSON object, got {type(parsed).__name__}: {content!r}. "
            f"Full response: {response.model_dump()}"
        )
        LOGGER.info(f"JSON mode OK — parsed: {parsed}")


    def verify_stop_sequences(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_stop_sequences", duration=duration)
        LOGGER.info("Verifying stop sequence handling")
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "Count from 1 to 10, separated by commas."},
        ]
        with self._api_call("/v1/chat/completions (stop)"):
            response = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                stop=[","],
                max_tokens=100,
                temperature=0,
            )
        self._assert_chat_completion_shape(response=response)
        content = response.choices[0].message.content or ""
        assert response.choices[0].finish_reason == "stop", (
            f"Expected finish_reason='stop', got '{response.choices[0].finish_reason}'. "
            f"Full response: {response.model_dump()}"
        )
        assert "," not in content, (
            f"Stop sequence ',' should not appear in output, got: {content!r}. Full response: {response.model_dump()}"
        )
        LOGGER.info(f"Stop sequences OK — content={content!r}")


    def verify_sampling_params(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_sampling_params", duration=duration)
        LOGGER.info("Verifying sampling parameters acceptance")
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "Say hello."},
        ]
        with self._api_call("/v1/chat/completions (temperature=0.8, top_p=0.9)"):
            response = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.8,
                top_p=0.9,
                max_tokens=10,
            )
        self._assert_chat_completion_shape(response=response)
        LOGGER.info(f"Sampling params OK — content={response.choices[0].message.content!r}")


    def verify_max_tokens(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_max_tokens", duration=duration)
        LOGGER.info("Verifying max_tokens enforcement")
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "Write a very long essay about the history of computing."},
        ]
        max_tokens = 5
        with self._api_call("/v1/chat/completions (max_tokens)"):
            response = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0,
            )
        self._assert_chat_completion_shape(response=response)
        assert response.usage is not None, (
            f"Usage is missing — cannot verify max_tokens. Full response: {response.model_dump()}"
        )
        assert response.usage.completion_tokens <= max_tokens, (
            f"completion_tokens ({response.usage.completion_tokens}) exceeded "
            f"max_tokens ({max_tokens}). Full response: {response.model_dump()}"
        )
        assert response.choices[0].finish_reason == "length", (
            f"Expected finish_reason='length' when hitting max_tokens, "
            f"got '{response.choices[0].finish_reason}'. Full response: {response.model_dump()}"
        )
        LOGGER.info(
            f"Max tokens OK — requested {max_tokens}, "
            f"got {response.usage.completion_tokens} completion tokens, "
            f"finish_reason='{response.choices[0].finish_reason}'"
        )


    def verify_logprobs(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_logprobs", duration=duration)
        LOGGER.info("Verifying logprobs support")
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "Say hello."},
        ]
        with self._api_call("/v1/chat/completions (logprobs)"):
            response = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=10,
                temperature=0,
                logprobs=True,
                top_logprobs=3,
            )
        self._assert_chat_completion_shape(response=response)
        logprobs = response.choices[0].logprobs
        assert logprobs is not None, f"logprobs is None despite logprobs=True. Full response: {response.model_dump()}"
        assert logprobs.content, f"logprobs.content is empty. Full response: {response.model_dump()}"
        for i, token_logprob in enumerate(logprobs.content):
            assert token_logprob.token is not None, (
                f"logprobs.content[{i}].token is None. Full response: {response.model_dump()}"
            )
            assert token_logprob.logprob is not None, (
                f"logprobs.content[{i}].logprob is None. Full response: {response.model_dump()}"
            )
            assert token_logprob.top_logprobs is not None, (
                f"logprobs.content[{i}].top_logprobs is None. Full response: {response.model_dump()}"
            )
            assert len(token_logprob.top_logprobs) <= 3, (
                f"logprobs.content[{i}].top_logprobs has {len(token_logprob.top_logprobs)} entries, "
                f"expected <= 3. Full response: {response.model_dump()}"
            )
        LOGGER.info(f"Logprobs OK — {len(logprobs.content)} token(s) with top_logprobs")


    def verify_n_completions(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_n_completions", duration=duration)
        LOGGER.info("Verifying n > 1 completions")
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "Pick a color."},
        ]
        n = 2
        with self._api_call("/v1/chat/completions (n=2)"):
            response = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                n=n,
                max_tokens=10,
                temperature=0.8,
            )
        dump = response.model_dump()
        assert response.id, f"Response has empty id. Full response: {dump}"
        assert len(response.choices) == n, f"Expected {n} choices, got {len(response.choices)}. Full response: {dump}"
        for i, choice in enumerate(response.choices):
            assert choice.index == i, f"Choice #{i}: expected index={i}, got {choice.index}. Full response: {dump}"
            assert choice.finish_reason, f"Choice #{i}: finish_reason is missing. Full response: {dump}"
            assert choice.message.content, f"Choice #{i}: content is empty. Full response: {dump}"
        LOGGER.info(f"N completions OK — {n} choices: {[c.message.content for c in response.choices]}")


    def verify_seed(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_seed", duration=duration)
        LOGGER.info("Verifying seed parameter acceptance")
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "Reply with exactly: 'deterministic'."},
        ]
        with self._api_call("/v1/chat/completions (seed)"):
            response = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                seed=42,
                max_tokens=10,
                temperature=0,
            )
        self._assert_chat_completion_shape(response=response)
        LOGGER.info(
            f"Seed OK — content={response.choices[0].message.content!r}, "
            f"system_fingerprint={response.system_fingerprint!r}"
        )


    def verify_error_forwarding(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_error_forwarding", duration=duration)
        LOGGER.info("Verifying proxy error forwarding (invalid model → proper error, not gateway error)")
        client = self._ensure_client()
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "test"},
        ]

        try:
            client.chat.completions.create(
                model="nonexistent-model-that-does-not-exist",
                messages=messages,
                max_tokens=1,
            )
            raise AssertionError("Request with invalid model name should have raised an error, but succeeded")
        except openai.NotFoundError as e:
            LOGGER.info(
                f"Error forwarding OK — invalid model returned HTTP {e.status_code}: "
                f"{e.response.text[:200] if e.response else 'N/A'}"
            )
        except openai.APIStatusError as e:
            assert e.status_code not in (502, 503, 504), (
                f"Proxy returned gateway error (HTTP {e.status_code}) instead of forwarding "
                f"the vLLM error. This suggests the proxy is swallowing the upstream error.\n"
                f"  Response: {e.response.text[:500] if e.response else 'N/A'}"
            )
            LOGGER.info(
                f"Error forwarding OK — invalid model returned HTTP {e.status_code} "
                f"(not a gateway error): {e.response.text[:200] if e.response else 'N/A'}"
            )


    def verify_tool_calling(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_tool_calling", duration=duration)
        LOGGER.info("Verifying tool calling support")
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "What is the weather like in Tokyo in celsius?"},
        ]
        with self._api_call("/v1/chat/completions (tool calling)"):
            response = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                tools=COMPAT_TEST_TOOLS,
                tool_choice="auto",
                max_tokens=100,
                temperature=0,
            )
        self._assert_chat_completion_shape(response=response)

        message = response.choices[0].message
        if message.tool_calls:
            self._assert_tool_calls_shape(tool_calls=message.tool_calls)
            LOGGER.info(
                f"Tool calling OK — {len(message.tool_calls)} tool call(s): "
                f"{[tc.function.name for tc in message.tool_calls]}"
            )
        else:
            LOGGER.info(f"Tool calling accepted (no tool_calls produced) — model returned text: {message.content!r}")


    def verify_tool_calling_streaming(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_tool_calling_streaming", duration=duration)
        LOGGER.info("Verifying tool calling with streaming")
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": "What is the weather in Paris?"},
        ]
        with self._api_call("/v1/chat/completions (tool calling + stream)"):
            stream = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                tools=COMPAT_TEST_TOOLS,
                tool_choice="auto",
                max_tokens=100,
                temperature=0,
                stream=True,
            )
            chunks = list(stream)

        assert len(chunks) > 0, "No stream chunks received for tool calling"

        tool_call_id: str | None = None
        function_name = ""
        function_arguments = ""
        content_parts: list[str] = []

        for chunk in chunks:
            assert chunk.object == "chat.completion.chunk"
            choice = chunk.choices[0]

            if choice.delta.content:
                content_parts.append(choice.delta.content)

            if choice.delta.tool_calls:
                tc = choice.delta.tool_calls[0]
                if tc.id:
                    tool_call_id = tc.id
                if tc.function and tc.function.name:
                    function_name += tc.function.name
                if tc.function and tc.function.arguments:
                    function_arguments += tc.function.arguments

        if tool_call_id:
            assert function_name, f"Streamed tool call has id={tool_call_id} but no function name"
            try:
                parsed_args = json.loads(function_arguments)
            except json.JSONDecodeError as e:
                raise AssertionError(
                    f"Streamed tool call arguments are not valid JSON.\n"
                    f"  function: {function_name}\n"
                    f"  raw arguments: {function_arguments!r}"
                ) from e
            LOGGER.info(f"Tool calling streaming OK — {function_name}({json.dumps(parsed_args)})")
        else:
            assembled = "".join(content_parts)
            assert len(assembled) > 0, (
                f"Streaming produced neither tool calls nor text content — received {len(chunks)} chunks"
            )
            LOGGER.info(f"Tool calling streaming accepted (no tool_calls) — text: {assembled!r}")


    def verify_parallel_tool_calls(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_parallel_tool_calls", duration=duration)
        LOGGER.info("Verifying parallel tool calls (multiple tool_calls in one response)")
        messages: list[ChatCompletionUserMessageParam] = [
            {
                "role": "user",
                "content": "What is the weather in both Tokyo and Paris right now?",
            },
        ]
        with self._api_call("/v1/chat/completions (parallel tool calls)"):
            response = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=messages,
                tools=COMPAT_TEST_TOOLS,
                tool_choice="auto",
                max_tokens=200,
                temperature=0,
            )
        self._assert_chat_completion_shape(response=response)

        message = response.choices[0].message
        if message.tool_calls and len(message.tool_calls) >= 2:
            self._assert_tool_calls_shape(tool_calls=message.tool_calls)
            LOGGER.info(
                f"Parallel tool calls OK — {len(message.tool_calls)} tool call(s): "
                f"{[tc.function.name for tc in message.tool_calls]}"
            )
        elif message.tool_calls:
            self._assert_tool_calls_shape(tool_calls=message.tool_calls)
            LOGGER.info(
                f"Parallel tool calls accepted (model produced {len(message.tool_calls)} "
                f"tool call instead of 2) — {[tc.function.name for tc in message.tool_calls]}"
            )
        else:
            LOGGER.info(
                f"Parallel tool calls accepted (no tool_calls produced) — model returned text: {message.content!r}"
            )


    def verify_multi_turn_tool_use(self, *, duration: int | None = None) -> None:
        if duration is not None:
            return self._loop_for_or_raise(verification="verify_multi_turn_tool_use", duration=duration)
        LOGGER.info("Verifying multi-turn tool use (agentic loop with tool results)")

        user_msg: ChatCompletionUserMessageParam = {
            "role": "user",
            "content": "What is the weather like in San Francisco?",
        }
        with self._api_call("/v1/chat/completions (tool use — turn 1)"):
            turn1 = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=[user_msg],
                tools=COMPAT_TEST_TOOLS,
                tool_choice="auto",
                max_tokens=100,
                temperature=0,
            )
        self._assert_chat_completion_shape(response=turn1)

        assistant_msg = turn1.choices[0].message
        if not assistant_msg.tool_calls:
            LOGGER.info(
                f"Multi-turn tool use: model did not produce tool_calls in turn 1 — "
                f"text: {assistant_msg.content!r}. Skipping turn 2."
            )
            return

        self._assert_tool_calls_shape(tool_calls=assistant_msg.tool_calls)
        tc = assistant_msg.tool_calls[0]
        LOGGER.info(f"Turn 1 produced tool call: {tc.function.name}({tc.function.arguments})")

        tool_call_param: ChatCompletionMessageToolCallParam = {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        }
        assistant_param: ChatCompletionAssistantMessageParam = {
            "role": "assistant",
            "tool_calls": [tool_call_param],
        }
        tool_result: ChatCompletionToolMessageParam = {
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps({"temperature": "18°C", "condition": "partly cloudy"}),
        }

        with self._api_call("/v1/chat/completions (tool use — turn 2)"):
            turn2 = self._ensure_client().chat.completions.create(
                model=self._model,
                messages=[user_msg, assistant_param, tool_result],
                tools=COMPAT_TEST_TOOLS,
                max_tokens=100,
                temperature=0,
            )
        self._assert_chat_completion_shape(response=turn2)

        turn2_content = turn2.choices[0].message.content or ""
        assert len(turn2_content) > 0, (
            f"Turn 2 produced empty text after tool result. Full response: {turn2.model_dump()}"
        )
        LOGGER.info(f"Multi-turn tool use OK — turn 2 response: {turn2_content!r}")


    @staticmethod
    def _assert_chat_completion_shape(response: ChatCompletion) -> None:
        dump = response.model_dump()
        assert response.id, f"Response has empty id. Full response: {dump}"
        assert response.object == "chat.completion", (
            f"Expected object='chat.completion', got '{response.object}'. Full response: {dump}"
        )
        assert response.model, f"Response has empty model field. Full response: {dump}"
        assert len(response.choices) > 0, f"Response has empty choices. Full response: {dump}"

        choice = response.choices[0]
        assert choice.index == 0, f"Expected first choice index=0, got {choice.index}. Full response: {dump}"
        assert choice.finish_reason, f"finish_reason is missing (got {choice.finish_reason!r}). Full response: {dump}"
        assert choice.message.role == "assistant", (
            f"Expected role='assistant', got '{choice.message.role}'. Full response: {dump}"
        )


    @staticmethod
    def _assert_usage_stats(response: ChatCompletion) -> None:
        dump = response.model_dump()
        assert response.usage is not None, f"Usage statistics are missing. Full response: {dump}"
        assert response.usage.prompt_tokens > 0, (
            f"prompt_tokens should be positive, got {response.usage.prompt_tokens}. Full response: {dump}"
        )
        assert response.usage.completion_tokens >= 0, (
            f"completion_tokens should be non-negative, got {response.usage.completion_tokens}. Full response: {dump}"
        )
        assert response.usage.total_tokens == (response.usage.prompt_tokens + response.usage.completion_tokens), (
            f"total_tokens ({response.usage.total_tokens}) != "
            f"prompt_tokens ({response.usage.prompt_tokens}) + "
            f"completion_tokens ({response.usage.completion_tokens}). "
            f"Full response: {dump}"
        )


    @staticmethod
    def _assert_tool_calls_shape(tool_calls: list[Any]) -> None:
        for i, tc in enumerate(tool_calls):
            assert tc.id, f"Tool call #{i} has empty id. Full tool_call: {tc.model_dump()}"
            assert tc.type == "function", (
                f"Tool call #{i}: expected type='function', got '{tc.type}'. Full tool_call: {tc.model_dump()}"
            )
            assert tc.function.name, f"Tool call #{i} has empty function name. Full tool_call: {tc.model_dump()}"
            try:
                json.loads(tc.function.arguments)
            except json.JSONDecodeError as e:
                raise AssertionError(
                    f"Tool call #{i} arguments are not valid JSON.\n"
                    f"  function: {tc.function.name}\n"
                    f"  raw arguments: {tc.function.arguments!r}\n"
                    f"  Full tool_call: {tc.model_dump()}"
                ) from e


