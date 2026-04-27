import sys

from .app import main as _app_main


def main() -> int:
    return _app_main()


if __name__ == "__main__":
    sys.exit(main())
