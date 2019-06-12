"""
Universal main function for invoking configurable functions.
"""

from textwrap import dedent

import argparse
import dynamake.application as da


def main() -> None:
    """
    Universal main function for invoking configurable functions.
    """
    da.main(argparse.ArgumentParser(description=dedent("""
        Execute some configurable Python functions.

        This can be used to execute arbitrary configurable functions. It requires you
        to specify one or more Python '-m module' to load the functions.
    """)))


if __name__ == '__main__':
    main()
