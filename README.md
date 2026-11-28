# Word counter

Prints the number of words, lines and characters in a text file, followed by
the most common words (case-insensitive).

## Usage

```
python wc.py FILE [--top N]
```

- `FILE` — the text file to read, or `-` to read standard input.
- `--top N` — how many of the most common words to print (default 5).

Examples:

```
python wc.py notes.txt
python wc.py notes.txt --top 10
cat notes.txt | python wc.py -
```

After `pip install .`, the same command is available as `wc-py`.

## Development

```
pip install -e .[dev]
python -m pytest -q
```
