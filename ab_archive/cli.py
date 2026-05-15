from __future__ import annotations

import argparse
from collections import OrderedDict
import sys

from .config import Config
from .db import ArchiveDB
from .ollama import OllamaClient
from .retrieval import embed_missing, hybrid_search
from .schedule import SYNC_HOUR, SYNC_MINUTE, install_schedule, remove_schedule, schedule_status
from .sync import sync_archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ab-archive", description="Local A/B test archive")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser("sync", help="Import/update Jira work items")
    sync_parser.add_argument("--no-embed", action="store_true", help="Skip local embeddings during sync")

    search_parser = sub.add_parser("search", help="Search the archive")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=8)

    ask_parser = sub.add_parser("ask", help="Ask a local AI question over the archive")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--limit", type=int, default=8)

    sub.add_parser("stats", help="Show archive stats")

    embed_parser = sub.add_parser("embed", help="Embed any unembedded chunks")
    embed_parser.add_argument("--limit", type=int, default=0, help="Reserved for future batching")

    schedule_parser = sub.add_parser("schedule", help="Manage daily scheduled sync")
    schedule_parser.add_argument("action", choices=["install", "remove", "status"])

    args = parser.parse_args(argv)
    config = Config.from_env()

    if args.command == "sync":
        try:
            issues, reports, embedded = sync_archive(
                config,
                embed=not args.no_embed,
                progress=lambda message: print(message, flush=True),
            )
        except RuntimeError as exc:
            print(f"Sync failed: {exc}", file=sys.stderr)
            return 1
        print(f"Synced {issues} Jira work items, {reports} Confluence reports, {embedded} embeddings.")
        return 0

    if args.command == "search":
        db = _open_db(config)
        ollama = _maybe_ollama(config)
        results = hybrid_search(db, args.query, ollama, limit=args.limit)
        _print_results(results)
        db.close()
        return 0

    if args.command == "ask":
        db = _open_db(config)
        ollama = OllamaClient(config.ollama_base_url, config.ollama_chat_model, config.ollama_embed_model)
        results = hybrid_search(db, args.question, ollama, limit=args.limit)
        if not results:
            print("No matching archived tests found.")
            return 0
        try:
            print(_answer(args.question, results, ollama))
        except RuntimeError as exc:
            print(f"Could not generate an AI answer: {exc}")
            print("Here are the best matching archived sources instead:")
            _print_results(results)
            db.close()
            return 1
        print("\nSources:")
        seen = OrderedDict()
        for result in results:
            seen.setdefault(result.key, result.source_url)
        for key, url in seen.items():
            print(f"- {key}: {url}")
        db.close()
        return 0

    if args.command == "stats":
        db = _open_db(config)
        for group, rows in db.stats().items():
            print(f"\n{group}:")
            for name, count in rows[:20]:
                print(f"  {name}: {count}")
        db.close()
        return 0

    if args.command == "embed":
        db = _open_db(config)
        ollama = OllamaClient(config.ollama_base_url, config.ollama_chat_model, config.ollama_embed_model)
        count = embed_missing(db, ollama)
        print(f"Embedded {count} chunks.")
        db.close()
        return 0

    if args.command == "schedule":
        if args.action == "install":
            path = install_schedule()
            print(f"Installed daily sync at {SYNC_HOUR:02d}:{SYNC_MINUTE:02d}: {path}")
        elif args.action == "remove":
            remove_schedule()
            print("Removed scheduled sync.")
        else:
            print(schedule_status())
        return 0

    return 1


def _open_db(config: Config) -> ArchiveDB:
    db = ArchiveDB(config.db_path)
    db.migrate()
    return db


def _maybe_ollama(config: Config) -> OllamaClient | None:
    return OllamaClient(config.ollama_base_url, config.ollama_chat_model, config.ollama_embed_model)


def _print_results(results) -> None:
    if not results:
        print("No matches.")
        return
    for index, result in enumerate(results, start=1):
        snippet = result.text.replace("\n", " ")
        if len(snippet) > 260:
            snippet = snippet[:257] + "..."
        print(f"{index}. {result.key} [{result.status}] {result.title}")
        print(f"   {result.source_url}")
        print(f"   {snippet}\n")


def _answer(question: str, results, ollama: OllamaClient) -> str:
    context = "\n\n".join(
        f"Source {index}: {result.key} [{result.status}] {result.title}\nURL: {result.source_url}\n{result.text}"
        for index, result in enumerate(results, start=1)
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You answer questions about a local A/B test archive. Be concise. "
                "Use only the provided sources. Mention Jira keys inline. "
                "If the sources do not answer the question, say that clearly."
            ),
        },
        {"role": "user", "content": f"Question: {question}\n\nSources:\n{context}"},
    ]
    return ollama.chat(messages)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
