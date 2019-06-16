"""
Universal main function for invoking DynaMake steps.
"""

import argparse
import dynamake.make as dm


def make() -> None:
    """
    Universal main function for invoking DynaMake build steps.
    """
    dm.make(argparse.ArgumentParser(description='Build some target(s) using DynaMake.'))


if __name__ == '__main__':
    make()
