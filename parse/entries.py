"""Fetch fresh listing pages (correctly spelled entry point)."""
import sys

from update import main


if __name__ == "__main__":
    sys.exit(main(["entries", *sys.argv[1:]]))
