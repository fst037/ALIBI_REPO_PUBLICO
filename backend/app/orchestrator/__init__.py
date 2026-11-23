"""The orchestration layer.

`app/services/*` holds the domain capabilities — ingestion (Module B),
Text-to-SQL (Module A), SQL execution over the data plane. This package holds
the thing that makes them one system: the steps that belong to no module in
particular, that read one module's output and shape it for the next or for the
user.

The separation is deliberate and load-bearing. Module A returns `sql_query` +
`result_table` and nothing else — it is a model specialised in SQL, not in
prose. Turning that result into a sentence a non-technical user understands is
the orchestrator's job (objective OE6: *communicate at all times what the system
is doing and what the user got, in business language rather than technical
terminology*).

Layout mirrors that idea:

    nodes/    one step of the graph — input → output, no hidden state
    prompts/  the system prompts those nodes send, as editable Markdown
    client.py the LLM seam, shared by every node

LLM calls here go through `client.complete()`, which uses the shared AWS Bedrock
transport (`app/core/bedrock.py`) — the same one Module A's client uses. Bedrock's
Converse API is model-agnostic, so which general model writes (Claude, Nova, …)
is one model id in configuration, never code. ALIBI is AWS-only.

Every node degrades rather than fails. With no model configured, each one still
returns a valid, deterministic result — so the whole flow runs offline, in tests
and on a laptop with no credentials, exactly like the ingestion pipeline and
Module A already do.
"""
