# Extraction Model

Pinegraf uses one model for extraction and LLM entity disambiguation. The default is
`gpt-5.4-mini`.

The earlier cascade was removed because cheap-model survivors polluted the graph and the savings were marginal. Extraction now favors graph quality over per-call cost.

To change models, set `EXTRACTION_MODEL` on both the `pinegraf-parse` Cloud Run Job and the
`pinegraf` service. If unset, both default to `gpt-5.4-mini`.

The heuristic fallback only runs when `OPENAI_API_KEY` is empty. That path logs a loud warning and should be treated as a local/dev fallback, not production extraction.
