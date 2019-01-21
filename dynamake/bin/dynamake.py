"""
Universal main function for invoking DynaMake steps.
"""

import argparse
from textwrap import dedent

import dynamake.make as dm


def main() -> None:
    """
    Universal main function for invoking DynaMake steps.
    """
    dm.main(argparse.ArgumentParser(description=dedent("""
        Execute some DynaMake step(s).

        This can be used to execute arbitrary DynaMake build scripts. It requires you
        either specify one or more Python '-m module' to load the actual build script
        steps, or specify a 'modules' configuration parameter for the '/' step in the
        YAML configuration file (by default, %s).
    """ % dm.Make.FILE)))


if __name__ == '__main__':
    main()
