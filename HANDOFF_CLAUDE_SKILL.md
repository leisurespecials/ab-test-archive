# Handoff: Create an A/B Test Archive Skill

## Goal

Create a Claude Code skill for an A/B test agent that automatically uses the local A/B Test Archive whenever the user is planning, writing, evaluating, or iterating on an A/B test.

The archive contains historical Jira A/B test work items from the `A/B Testing` project and is meant to prevent repeated ideas, surface prior learnings, and help shape stronger new test hypotheses.

## Existing Archive

Project path:

```text
/Users/lindseybateman/Documents/ab-test-archive
```

Database:

```text
/Users/lindseybateman/Documents/ab-test-archive/data/ab_archive.sqlite
```

Main CLI usage:

```bash
cd /Users/lindseybateman/Documents/ab-test-archive
python3 -m ab_archive ask "which checkout tests were successful?"
python3 -m ab_archive search "checkout date picker"
python3 -m ab_archive stats
```

If the local virtual environment is activated or on PATH, this also works:

```bash
ab-archive ask "which property page tests were successful?"
ab-archive search "sticky CTA mobile property"
ab-archive stats
```

The archive uses local SQLite plus local Ollama embeddings/chat. If Ollama is unavailable, search/fallback sources still work.

## Data Scope

The archive intentionally contains only Jira work items matching:

```jql
project = AB
AND issuetype = "AB Test"
AND status in (Implemented, "No significant Result", Successful, Unsuccessful)
```

Important behavior:

- Treat `Successful` and `Implemented` together when the user asks for “successful”, “winning”, or “worked well” tests.
- Treat `Unsuccessful` as failed/lost tests.
- Treat `No significant Result` as inconclusive/no clear effect.
- Do not use `A/B Testing BS` / `ATB`.

## Useful DB Views

If the skill reads SQLite directly, prefer these views:

- `test_overview` - one readable row per A/B test.
- `document_overview` - Jira and Confluence source documents.
- `chunk_overview` - RAG chunks joined back to test name and document title, without embedding vectors.

Avoid showing the raw `chunks.embedding_json` field to users. It is only for vector search.

## When the Skill Should Trigger

Use the archive whenever the user is:

- Drafting a new A/B test ticket.
- Asking whether an idea has been tested before.
- Looking for examples from checkout, funnel, property page, homepage, search/lister, campaign, pricing, extras, date picker, CTA, urgency, trust, reviews, filters, or mobile/desktop flows.
- Asking for likely risks, past learnings, expected impact, or related experiments.
- Trying to decide whether to implement, iterate, or avoid an idea.
- Writing a hypothesis, trigger rules, KPI/tracking plan, or result interpretation.

Do not wait for the user to explicitly say “search the archive” if the task is clearly about creating or evaluating an A/B test. Consult the archive proactively.

## Desired Agent Workflow

When helping with a new test:

1. Identify the test context:
   - Market/brand, e.g. `HSNL`, `HSDE`, `BSNL`.
   - Page or journey area, e.g. property, funnel, checkout, homepage, search/lister.
   - Feature theme, e.g. price clarity, extras, date picker, CTA copy, urgency, filters.

2. Query the archive with 2-4 targeted searches:
   - Broad page area: `property price tooltip`, `checkout extras`, etc.
   - User problem/theme: `date picker friction`, `sticky CTA`, `price breakdown`.
   - Outcome-specific query: `successful property price`, `unsuccessful funnel extras`, `no significant CTA`.

3. Synthesize results:
   - Mention Jira keys.
   - Separate wins, losses, inconclusive tests, and implemented tests.
   - Identify repeated patterns and contradictions.
   - Do not overstate evidence. If prior results are weak, say so.

4. Apply the learning to the new test:
   - Recommend whether this idea looks new, repeated, promising, risky, or better as an iteration.
   - Suggest a sharper hypothesis.
   - Suggest primary/secondary KPIs.
   - Suggest trigger rules and segmentation to avoid known pitfalls.
   - Reference relevant prior tests in the ticket.

## Output Style

When the archive is used, answer in a concise product/experimentation style:

```text
I found 6 related historical tests.

Relevant wins / implemented:
- AB-2882 ...
- AB-2862 ...

Relevant inconclusive or negative tests:
- AB-2885 ...
- AB-2899 ...

What this suggests:
...

Recommended direction for the new test:
...
```

Avoid dumping long raw chunks. Summarize the learning and include source keys/links.

## Example Archive Commands

Use these as command patterns:

```bash
cd /Users/lindseybateman/Documents/ab-test-archive
python3 -m ab_archive ask "which property page tests about price clarity were successful?"
python3 -m ab_archive ask "what checkout or funnel tests involving extras were unsuccessful?"
python3 -m ab_archive search "sticky CTA mobile property date picker"
python3 -m ab_archive stats
```

For direct SQLite inspection:

```bash
sqlite3 /Users/lindseybateman/Documents/ab-test-archive/data/ab_archive.sqlite \
  "SELECT key, name, status, market, page_type, jira_url FROM test_overview WHERE page_type='property' AND status IN ('Successful','Implemented') LIMIT 20;"
```

## Acceptance Criteria For The Skill

The skill is good when:

- It automatically checks the archive before proposing a new A/B test.
- It uses both `Successful` and `Implemented` for “successful/winning” questions.
- It cites Jira keys for relevant prior tests.
- It does not expose embedding vectors or raw chunk noise.
- It turns archive findings into practical test design recommendations.
- It gracefully says when no close historical precedent was found.
- It keeps the final answer useful for writing or improving a Jira A/B test ticket.

## Suggested Skill Name

`ab-test-archive-consultant`

Suggested skill description:

> Use the local A/B Test Archive to find historical experiments, outcomes, and learnings before planning or evaluating a new A/B test. Trigger when drafting test hypotheses, checking if an idea was tried before, choosing KPIs, interpreting results, or looking for related wins/losses.
