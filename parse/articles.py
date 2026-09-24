"""Download and validate missing article pages without modifying published data."""
import sys

from update import main


if __name__ == "__main__":
    sys.exit(main(["download", *sys.argv[1:]]))
