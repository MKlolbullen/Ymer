"""Command-line entry point for wc.py."""

import argparse
import sys

from app.counter import TextStats, analyze


def positive_int(value: str) -> int:
    """argparse type for --top: an integer of at least 1."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}")
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wc.py",
        description="Count words, lines and characters, and show the most common words.",
    )
    parser.add_argument("file", metavar="FILE", help="text file to read, or - for standard input")
    parser.add_argument(
        "--top",
        metavar="N",
        type=positive_int,
        default=5,
        help="how many of the most common words to print (default 5)",
    )
    return parser


def read_text(path: str) -> str:
    """Read the whole file as UTF-8, or standard input when path is '-'."""
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def format_stats(stats: TextStats) -> str:
    lines = [
        f"words: {stats.words}",
        f"lines: {stats.lines}",
        f"characters: {stats.characters}",
        "top words:",
    ]
    lines.extend(f"{word} {count}" for word, count in stats.top_words)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the word counter. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    try:
        text = read_text(args.file)
    except (OSError, UnicodeDecodeError) as error:
        name = "standard input" if args.file == "-" else args.file
        print(f"wc.py: cannot read {name}: {error}", file=sys.stderr)
        return 1
    print(format_stats(analyze(text, args.top)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
