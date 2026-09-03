"""Model access, behind an interface — and the stubs that prove the boundary.

Every model call in this system goes through ModelProvider. That exists for one
reason: the architecture's central claim is that a model PROPOSES and
deterministic arithmetic DISPOSES, and a claim like that is only worth making if
it can be tested with proposals chosen by an adversary rather than by the model.

AdversarialProvider returns confidently wrong answers. If the pipeline's output
is unchanged when it is swapped in, the boundary holds. If any number moves, the
boundary leaks — and that is a stronger test than any live model, because a live
model's mistakes are whatever it happens to make, not what would hurt most.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

# Benchmarked against the strongest available model deliberately. Using a weak
# model to test "should an LLM do the matching?" would rig the answer toward the
# conclusion this codebase already holds.
BENCHMARK_MODEL = "claude-opus-5"


@dataclass
class ModelReply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    model: str = ""
    error: Optional[str] = None


class ModelProvider(Protocol):
    name: str

    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024) -> ModelReply:
        ...


class AnthropicProvider:
    """Live provider. Constructed lazily so the absence of a key is a runtime
    error at the point of use, never a silent fallback to a stub — a benchmark
    that quietly ran against a stub would be worse than no benchmark."""

    name = "anthropic"

    def __init__(self, model: str = BENCHMARK_MODEL, temperature_note: str = ""):
        import anthropic
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        # The SDK resolves credentials lazily: Anthropic() constructs fine with no
        # key and only fails on the first request. Constructing successfully must
        # therefore NOT be read as "the model is available" -- that is precisely
        # how a benchmark ends up silently measuring nothing. Check now.
        if not (getattr(self._client, "api_key", None)
                or getattr(self._client, "auth_token", None)):
            raise RuntimeError(
                "Anthropic client constructed but no credential resolved "
                "(no ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or auth profile). "
                "Refusing to present an unauthenticated client as available.")
        self.model = model

    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024) -> ModelReply:
        t0 = time.perf_counter()
        try:
            r = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:                       # surfaced, never swallowed
            return ModelReply(text="", latency_s=time.perf_counter() - t0,
                              model=self.model, error=f"{type(e).__name__}: {e}")
        text = "".join(b.text for b in r.content if b.type == "text")
        return ModelReply(text=text, input_tokens=r.usage.input_tokens,
                          output_tokens=r.usage.output_tokens,
                          latency_s=time.perf_counter() - t0, model=self.model)


class ScriptedProvider:
    """Returns canned replies in order. Deterministic; for boundary tests."""

    name = "scripted"

    def __init__(self, replies: List[str]):
        self._replies = list(replies)
        self._i = 0

    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024) -> ModelReply:
        r = self._replies[self._i % len(self._replies)]
        self._i += 1
        return ModelReply(text=r, model="scripted")


class AdversarialProvider:
    """Confidently wrong, in the shapes that would do most damage.

    Not random noise: each reply is well-formed, plausible, and incorrect in a
    way that would corrupt the books if trusted. A boundary that survives this
    survives a model having a bad day.
    """

    name = "adversarial"

    def __init__(self, counterparties: List[str], references: List[str]):
        self._cp = sorted(counterparties)
        self._ref = sorted(references)
        self._i = 0

    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024) -> ModelReply:
        self._i += 1
        # a real-looking entity that is simply the wrong one, at high confidence
        payload = {
            "counterparty": self._cp[self._i % len(self._cp)] if self._cp else "ACME PVT LTD",
            "reference": self._ref[self._i % len(self._ref)] if self._ref else "UTR00000000",
            "confidence": 0.99,
            "reasoning": "clear match on counterparty name and reference format",
        }
        return ModelReply(text=json.dumps(payload), model="adversarial")


class GeminiProvider:
    """Secondary provider, for a CROSS-PROVIDER data point only.

    Deliberately not the primary benchmark target. The benchmark's claim is
    "a model should not make the match decision", and measuring that against a
    free-tier or throttled model would support only the narrower claim "THIS
    model should not" -- which is the stack-the-deck failure this phase was
    explicitly warned about. Its value is as a second data point ALONGSIDE a
    frontier model, showing the finding is not provider-specific.
    """

    name = "gemini"

    def __init__(self, model: str = "gemini-pro-latest", api_key: Optional[str] = None):
        from google import genai
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("no GEMINI_API_KEY resolved")
        self._client = genai.Client(api_key=key)
        self.model = model

    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024) -> ModelReply:
        t0 = time.perf_counter()
        try:
            r = self._client.models.generate_content(
                model=self.model, contents=f"{system}\n\n{prompt}")
        except Exception as e:
            return ModelReply(text="", latency_s=time.perf_counter() - t0,
                              model=self.model, error=f"{type(e).__name__}: {e}")
        u = getattr(r, "usage_metadata", None)
        return ModelReply(
            text=r.text or "",
            input_tokens=getattr(u, "prompt_token_count", 0) or 0,
            output_tokens=getattr(u, "candidates_token_count", 0) or 0,
            latency_s=time.perf_counter() - t0, model=self.model)


class OpenAIProvider:
    """Frontier non-Anthropic provider — the benchmark's primary target.

    Chosen deliberately rather than by availability. Two constraints had to be
    met and this satisfies both:

      NOT WEAK. Benchmarking "should a model make the match decision?" against a
      throttled or small model would support only the narrower claim that THAT
      model should not, which is how one builds a benchmark that confirms what
      one already believes.

      NOT THE VENDOR THIS WAS BUILT WITH. This project was written using Claude
      Code. Measuring the claim against Anthropic's own model invites the obvious
      objection that the test was run on the friendly vendor. A result that holds
      on a competitor's frontier model is harder to dismiss.

    temperature is deliberately NOT set. No claim is made that any setting
    guarantees identical output; self-disagreement is measured empirically across
    runs instead of assumed away.
    """

    name = "openai"

    def __init__(self, model: str = "gpt-5.2", api_key: Optional[str] = None,
                 temperature: Optional[float] = None):
        from openai import OpenAI
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("no OPENAI_API_KEY resolved")
        self._client = OpenAI(api_key=key)
        self.model = model
        # None => the parameter is not sent at all and the API default applies.
        # A float => pinned, and RECORDED as pinned, so the report can state what
        # was configured separately from what determinism actually resulted.
        self.temperature = temperature

    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024) -> ModelReply:
        t0 = time.perf_counter()
        try:
            kw = {}
            if self.temperature is not None:
                kw["temperature"] = self.temperature
            r = self._client.chat.completions.create(
                model=self.model,
                max_completion_tokens=max_tokens,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
                **kw,
            )
        except Exception as e:
            return ModelReply(text="", latency_s=time.perf_counter() - t0,
                              model=self.model, error=f"{type(e).__name__}: {e}")
        u = r.usage
        return ModelReply(
            text=r.choices[0].message.content or "",
            input_tokens=getattr(u, "prompt_tokens", 0) or 0,
            output_tokens=getattr(u, "completion_tokens", 0) or 0,
            latency_s=time.perf_counter() - t0, model=self.model)


def load_dotenv(path: str = ".env") -> None:
    """Load .env if present. Never logs values."""
    p = os.path.join(os.getcwd(), path)
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def default_provider(require_live: bool = False) -> Optional[ModelProvider]:
    """Live provider if credentials exist, else None. Never a silent stub."""
    try:
        return AnthropicProvider()
    except Exception as e:
        if require_live:
            raise RuntimeError(
                "no live model credentials; refusing to substitute a stub. "
                f"({type(e).__name__}: {e})") from e
        return None
