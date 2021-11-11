"""
Main Program
"""

import argparse
import os
import sys

from dynamake import make


def main() -> None:
    """
    Universal main function for invoking DynaMake steps.
    """
    sys.path.append(os.getcwd())
    make(argparse.ArgumentParser(description="Build some target(s) using DynaMake."), logger_name=sys.argv[0])


if __name__ == "__main__":
    main()
