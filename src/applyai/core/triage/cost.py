"""Token counts and dollars: what a run will roughly cost, and what it did cost.

The estimate is deliberately on the high side, because the hard cap is checked
against it before anything is sent:
  * tokens are counted as characters / 3 (current Claude tokenizers average a
    little more than 3 characters per token on English);
  * each request also pays REQUEST_OVERHEAD tokens for the schema and framing;
  * in a batch, cache hits are not guaranteed, so every request is assumed to
    pay full price for the system block.
Actual cost comes from the usage the API reports.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from applyai.config import Prices

CHARS_PER_TOKEN = 3
REQUEST_OVERHEAD = 600


@dataclass
class Usage:
    """Tokens one request (or a whole run) used, split the way they are billed."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    @classmethod
    def from_api(cls, usage) -> Usage:
        """From an Anthropic `usage` object (missing cache fields count as 0)."""
        return cls(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.cache_read_tokens += other.cache_read_tokens


def cost_usd(usage: Usage, prices: Prices, *, batch: bool) -> float:
    """Dollars for `usage` at these prices (Batch API: times the batch discount)."""
    total = (usage.input_tokens * prices.input + usage.output_tokens * prices.output
             + usage.cache_write_tokens * prices.cache_write_1h
             + usage.cache_read_tokens * prices.cache_read) / 1_000_000
    return total * (prices.batch_discount if batch else 1.0)


def tokens(text: str) -> int:
    """A high-side token count for `text`."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class Estimate:
    jobs: int
    input_tokens: int
    output_tokens: int
    usd: float


def request_estimate(system: str, user: str, prices: Prices, output_tokens: int, *,
                     batch: bool, first: bool) -> Usage:
    """Estimated usage of one request.

    Synchronous runs write the system block to the cache on the first request
    and read it after; batches are assumed to pay it in full every time.
    """
    system_tokens = tokens(system)
    usage = Usage(input_tokens=tokens(user) + REQUEST_OVERHEAD, output_tokens=output_tokens)
    if batch:
        usage.input_tokens += system_tokens
    elif first:
        usage.cache_write_tokens = system_tokens
    else:
        usage.cache_read_tokens = system_tokens
    return usage


def estimate(system: str, users: list[str], prices: Prices, output_tokens: int, *, batch: bool) -> Estimate:
    """The whole run's estimate, for the confirmation prompt and the cap."""
    total = Usage()
    for index, user in enumerate(users):
        total.add(request_estimate(system, user, prices, output_tokens, batch=batch, first=index == 0))
    return Estimate(
        jobs=len(users),
        input_tokens=total.input_tokens + total.cache_write_tokens + total.cache_read_tokens,
        output_tokens=total.output_tokens,
        usd=cost_usd(total, prices, batch=batch),
    )
