"""
Manage per-step configurations.
"""

import os
from hashlib import md5
from typing import Any
from typing import Dict
from typing import List
from uuid import UUID

import yaml

from .patterns import Pattern


class Rule:  # pylint: disable=too-few-public-methods
    """
    A single configuration rule.
    """

    def __init__(self, path: str, index: int,
                 when: Dict[str, Any], then: Dict[str, Any]) -> None:
        """
        Create a configuration rule.
        """

        #: The path of the file this was loaded from.
        self.path = path

        #: The index of this rule in the file.
        self.index = index

        #: The conditions for when to apply the rule.
        self.when = when

        #: The parameter values provided by the rule.
        self.then = then

    def is_match(self, context: List[str], arguments: Dict[str, Any]) -> bool:
        """
        Whether the rule is a match for a step with the specified arguments.
        """
        if ('step' in self.when and not self._step_is_match(context)) \
                or ('context' in self.when and not self._context_is_match(context)):
            return False
        for key, condition in self.when.items():
            if key not in ['step', 'context'] \
                    and not self._key_is_match(key, condition, context, arguments):
                return False
        return True

    def is_specific(self) -> bool:
        """
        Whether the rule applies to specific steps.

        If it does, then we'll complain about unrecognized parameters.
        """
        return 'step' in self.when or 'context' in self.when

    def _step_is_match(self, context: List[str]) -> bool:
        step = context[-1]
        condition = self.when['step']
        if isinstance(condition, list):
            return step in condition
        return step == condition

    def _context_is_match(self, context: List[str]) -> bool:
        condition = self.when['context']
        try:
            for pattern in condition:
                if pattern.is_match(context):
                    return True
            return False
        except BaseException:
            return condition.is_match(context)

    def _key_is_match(self, key: str, condition: Any,
                      context: List[str], arguments: Dict[str, Any]) -> bool:
        if key.startswith('lambda '):
            return Rule._match_lambda(key[7:],  # pylint: disable=protected-access
                                      condition, arguments)

        if key not in arguments:
            if self.is_specific():
                raise RuntimeError('Unknown parameter: %s '
                                   'for the step: %s '
                                   'in the rule: %s '
                                   'of the file: %s'
                                   % (key, '.'.join(context), self.index, self.path))
            return False

        try:
            return arguments[key] in condition
        except BaseException:
            return arguments[key] == condition

    @staticmethod
    def _match_lambda(parameters: str, condition: str, arguments: Dict[str, Any]) -> bool:
        parameter_values: Dict[str, Any] = {}
        parameter_names = [parameter_name.strip() for parameter_name in parameters.split(',')]
        for parameter_name in parameter_names:
            if parameter_name not in arguments:
                return False
            parameter_values[parameter_name] = arguments[parameter_name]
        evaluator = eval('lambda %s: %s' % (parameters, condition))  # pylint: disable=eval-used
        return evaluator(**parameter_values)

    @staticmethod
    def _load_rule(path: str, index: int, rule: Any) -> 'Rule':
        if not isinstance(rule, dict):
            raise RuntimeError('Non-mapping rule: %s '
                               'in the file: %s'
                               % (index, path))

        data = {key: Rule._load_value(path, index, rule, key)  # pylint: disable=protected-access
                for key in ['when', 'then']}

        for key in rule:
            raise RuntimeError('Unknown key: %s '
                               'in the rule: %s '
                               'in the file: %s'
                               % (key, index, path))

        when = data['when']
        if 'context' in when:
            try:
                if isinstance(when['context'], list):
                    when['context'] = [Pattern(pattern) for pattern in when['context']]
                else:
                    when['context'] = Pattern(when['context'])
            except BaseException:
                raise RuntimeError('Invalid pattern(s) for context '
                                   'in the rule: %s '
                                   'in the file: %s'
                                   % (index, path))
        return Rule(path, index, **data)

    @staticmethod
    def _load_value(path: str, index: int, rule: Dict[str, Any], key: str) -> Dict[str, Any]:
        if key not in rule:
            raise RuntimeError('Missing key: %s '
                               'in the rule: %s '
                               'in the file: %s'
                               % (key, index, path))

        value = rule[key]
        del rule[key]

        if not isinstance(value, dict):
            raise RuntimeError('Value is not a mapping '
                               'in the key: %s '
                               'in the rule: %s '
                               'in the file: %s'
                               % (key, index, path))

        for sub_key in value:
            if not isinstance(sub_key, str):
                raise RuntimeError('Sub-key is not a string: %s '
                                   'in the key: %s '
                                   'in the rule: %s '
                                   'in the file: %s'
                                   % (sub_key, key, index, path))

        return value

    @staticmethod
    def load(path: str) -> List['Rule']:
        """
        Load configuration rules from a YAML file.
        """
        with open(path, 'r') as file:
            data = yaml.load(file.read())

        if data is None:
            data = []

        if not isinstance(data, list):
            raise RuntimeError('The file: %s '
                               'does not contain a top-level sequence'
                               % path)

        return ([Rule._load_rule(path, index, rule)  # pylint: disable=protected-access
                 for index, rule in enumerate(data)])


class Config:
    """
    Snakemake integration class.
    """

    #: All the known configuration rules (including for per-rule parameters).
    rules: List[Rule]

    #: The directory where to create the per-rule configuration files.
    DIRECTORY: str

    @staticmethod
    def load(path: str) -> None:
        """
        Load a YAML configuration file into `:py:attr:`snakeconf.config.Config.rules`.

        The rules from the loaded files will override any values specified by previously loaded
        rules. In general, the last matching rule wins.
        """
        Config.rules += Rule.load(path)

    @staticmethod
    def path_for_step(context: List[str], arguments: Dict[str, Any]) -> str:
        """
        Return the path name of a configuration file for a specific step invocation.

        The file name is based on a digest of the full invocation context and all the arguments.
        """
        digester = md5()
        digester.update(yaml.dump(context).encode('utf-8'))
        digester.update(yaml.dump(arguments).encode('utf-8'))
        return os.path.join(Config.DIRECTORY, 'config.%s.yaml' % UUID(bytes=digester.digest()))

    @staticmethod
    def values_for_step(context: List[str], arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return the configuration values for a specific step invocation.
        """
        values: Dict[str, Any] = {}
        for rule in Config.rules:
            if rule.is_match(context, arguments):
                values.update(rule.then)
        return values

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Config.rules = []
        Config.DIRECTORY = '.dynamake'


Config.reset()
