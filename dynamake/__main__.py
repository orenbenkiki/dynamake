"""
Main Program
"""

from dynamake import make

import argparse
import sys


def main() -> None:
    """
    Universal main function for invoking DynaMake steps.
    """
    make(argparse.ArgumentParser(description='Build some target(s) using DynaMake.'),
         logger_name=sys.argv[0])


if __name__ == '__main__':
    main()
