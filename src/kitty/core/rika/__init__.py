"""Rika, rika!'s own agent (docs/rika-design.md, phase 1).

* :mod:`~kitty.core.rika.router`: the fast/standard/deep tier router;
* :mod:`~kitty.core.rika.tools`: tools wrapping core functions, with JSON-schema args,
  a reversible flag and an undo payload;
* :mod:`~kitty.core.rika.commands`: deterministic phrases that run tools without a model
  (also the demo's scripted responder);
* :mod:`~kitty.core.rika.llm`: OpenRouter tool-calling with streaming, under the shared
  AI key, daily budget and price ceilings;
* :mod:`~kitty.core.rika.agents`: worker agents in herdr panes and Tern windows;
* :mod:`~kitty.core.rika.store`: messages, actions and approvals (migration 037);
* :mod:`~kitty.core.rika.loop`: the agent loop, approvals and undo; it publishes events
  to the page's WebSocket through :class:`~kitty.core.rika.loop.Hub`.
"""
