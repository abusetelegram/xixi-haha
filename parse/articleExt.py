"""Extract cached articles, preserving the legacy full and minimal outputs."""
import sys

from update import main


if __name__ == "__main__":
    sys.exit(main(["extract", "--write-full", *sys.argv[1:]]))
