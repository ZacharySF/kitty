"""Stage 3: LLM triage (tiers, resume pick, edit notes).

run.py talks to the model; rules.py gives the free tiers and is imported by
discovery, so this package imports nothing itself (the Anthropic SDK is slow
to import and discovery does not need it).
"""
