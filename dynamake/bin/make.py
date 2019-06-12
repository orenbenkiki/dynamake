"""
Universal main function for invoking DynaMake steps.
"""

from textwrap import dedent

import argparse
import dynamake.make as dm


def make() -> None:
    """
    Universal main function for invoking DynaMake build steps.
    """
    dm.make(argparse.ArgumentParser(description=dedent("""
        Execute some DynaMake step(s).

        This can be used to execute arbitrary DynaMake build steps. It requires you
        to specify one or more Python '-m module' to load the actual build steps.
    """)))


if __name__ == '__main__':
    make()
