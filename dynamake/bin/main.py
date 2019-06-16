"""
Universal main function for invoking configurable functions.
"""

import argparse
import dynamake.application as da


def main() -> None:
    """
    Universal main function for invoking configurable functions.
    """
    da.main(argparse.ArgumentParser(description='Execute configurable Python functions.'))


if __name__ == '__main__':
    main()
