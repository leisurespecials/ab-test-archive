# Local A/B Test Archive

A local CLI archive for the Jira project `A/B Testing` (`AB`). It imports only work items of type `AB Test` with one of these statuses:

- `Implemented`
- `No significant Result`
- `Successful`
- `Unsuccessful`

The archive stores Jira and linked Confluence report text in SQLite, supports direct search and stats, and can answer questions with a fully local Ollama model.

## Setup

1. Copy `.env.example` to `.env` and fill in `JIRA_EMAIL` and `JIRA_API_TOKEN`.
2. Install Ollama if you want AI answers and embeddings.
3. Pull the local models:

```bash
ollama pull nomic-embed-text
ollama pull llama3.1:8b
```

You can run the archive directly from this folder without installing anything:

```bash
python3 -m ab_archive sync
python3 -m ab_archive ask "what checkout tests did we run?"
```

If you want the shorter `ab-archive` command, install it into a local virtual environment. This avoids Homebrew Python's `externally-managed-environment` error:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
ab-archive --help
```

When you open a new terminal later, activate the environment again before using `ab-archive`:

```bash
cd /Users/lindseybateman/Documents/ab-test-archive
source .venv/bin/activate
```

## Commands

Run from this folder:

```bash
python3 -m ab_archive sync
python3 -m ab_archive search "checkout"
python3 -m ab_archive ask "what checkout tests did we run?"
python3 -m ab_archive stats
python3 -m ab_archive schedule install
```

If you install the package later, the same commands are available as `ab-archive`.

During sync you should see live progress like:

```text
Opening local archive database: ...
Starting Jira sync for project AB / work type AB Test / final statuses only.
Fetching Jira page 1...
Fetched 100 Jira work items from page 1.
Processing AB-2895 [Successful]: HSNL - Property - Improve usability of total price tooltip
  Found 1 linked Confluence report(s).
  Fetching Confluence page 6172180484...
Saved progress: 25 Jira work items, 7 Confluence reports.
```

For a faster first import, you can skip embeddings and add them later:

```bash
python3 -m ab_archive sync --no-embed
python3 -m ab_archive embed
```

## Data

The default database is `data/ab_archive.sqlite`. No Jira or Confluence data is sent to a cloud LLM. Jira and Confluence are only read during sync; answers and embeddings use your local Ollama instance.

For visual browsing in DB Browser for SQLite or TablePlus, start with these views:

- `test_overview` - one readable row per A/B test, including name, status, market, page type, and result previews.
- `document_overview` - Jira and Confluence source documents with readable titles and previews.
- `chunk_overview` - RAG chunks joined back to the test name and document title, without the embedding vector column.

The raw `chunks` table is meant for the search engine, not for human browsing. It stores partial text slices and embedding vectors, so rows often start mid-sentence.

The scheduled sync runs daily at 09:00 when your Mac is on.
