-- The OpenRouter models (ai_models, Settings → AI's catalog) that accept the ``reasoning``
-- parameter, from the same /models refresh: Rika's thinking chip offers effort levels only
-- for these (kitty.core.rika.brain.catalog), and only these get ``reasoning`` in a request.
-- Kept apart from ai_models so its rows and columns stay as they were.
CREATE TABLE ai_model_reasoning (
    id TEXT PRIMARY KEY
);
