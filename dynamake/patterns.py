"""
Allow simple loading of regular expressions in configuration YAML files.
"""

import re
from typing import List
from typing.re import Pattern  # pylint: disable=import-error

import yaml
from yaml import Loader
from yaml import Node


def glob2re(glob: str) -> Pattern:  # pylint: disable=too-many-branches
    """
    Translate a ``glob`` pattern to the equivalent ``re.Pattern``.
    """

    index = 0
    size = len(glob)
    results: List[str] = []

    while index < size:
        char = glob[index]
        index = index + 1

        if char == '*':
            if index < size and glob[index] == '*':
                index += 1
                if results and results[-1] == '\\/' and index < size and glob[index] == '/':
                    results.append('(.*/)?')
                    index += 1
                else:
                    results.append('.*')
            else:
                results.append('[^/]*')

        elif char == '?':
            results.append('[^/]')

        elif char == '[':
            end_index = index
            while end_index < size and glob[end_index] != ']':
                end_index += 1

            if end_index >= size:
                results.append('\\[')

            else:
                characters = glob[index:end_index].replace('\\', '\\\\')
                index = end_index + 1

                results.append('[')

                if characters[0] == '!':
                    results.append('^/')
                    characters = characters[1:]
                elif characters[0] == '^':
                    results.append('\\')

                results.append(characters)
                results.append(']')

        else:
            results.append(re.escape(char))

    return re.compile(''.join(results))


def _load_glob(loader: Loader, node: Node) -> Pattern:
    return glob2re(loader.construct_scalar(node))


yaml.add_constructor('!g', _load_glob)


def _load_regexp(loader: Loader, node: Node) -> Pattern:
    return re.compile(loader.construct_scalar(node))


yaml.add_constructor('!r', _load_regexp)
