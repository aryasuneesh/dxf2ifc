import sys

from .cli import main

if __name__ == "__main__":  # the guard matters on Windows: triage and batch start worker processes
    sys.exit(main())
