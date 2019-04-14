"""
Manage per-step configurations.
"""

from hashlib import md5
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing.re import Pattern  # pylint: disable=import-error
from uuid import UUID

import os
import yaml


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

    _BUILTINS = ['step', 'stack']

    def is_match(self, context: Dict[str, Any]) -> bool:
        """
        Whether the rule is a match for a step invocation.
        """
        return self._match_step_and_stack(context) \
            and self._verify_known_parameters(context) \
            and self._match_other_conditions(context)

    def _match_step_and_stack(self, context: Dict[str, Any]) -> bool:
        for key in Rule._BUILTINS:
            if key in self.when and not self._key_is_match(key, self.when[key], context):
                return False
        return True

    def _verify_known_parameters(self, context: Dict[str, Any]) -> bool:
        for key in self.when:
            if key in Rule._BUILTINS:
                continue
            if key.startswith('lambda '):
                for name in key[7:].split(','):
                    self._parameter_name(name.strip(), context)
            else:
                self._parameter_name(key, context)
        return True

    def _match_other_conditions(self, context: Dict[str, Any]) -> bool:
        for key, condition in self.when.items():
            if key not in Rule._BUILTINS and not self._key_is_match(key, condition, context):
                return False
        return True

    def _key_is_match(self, key: str, condition: Any, context: Dict[str, Any]) -> bool:
        if key.startswith('lambda '):
            return self._match_lambda(key[7:],  # pylint: disable=protected-access
                                      condition, context)
        parameter_name = self._parameter_name(key, context)
        return parameter_name is not None and Rule._match_value(context[parameter_name], condition)

    def _match_lambda(self, arguments: str, condition: str, context: Dict[str, Any]) -> bool:
        parameter_values: Dict[str, Any] = {}
        for argument in arguments.split(','):
            parameter_name = self._parameter_name(argument.strip(), context)
            if parameter_name is None:
                return False
            parameter_values[parameter_name] = context[parameter_name]
        evaluator = eval('lambda %s: %s'  # pylint: disable=eval-used
                         % (arguments.replace('?', ''), condition))
        return evaluator(**parameter_values)

    def _parameter_name(self, parameter_name: str, context: Dict[str, Any]) -> Optional[str]:
        is_optional = parameter_name.endswith('?')
        if is_optional:
            parameter_name = parameter_name[:-1]

        if parameter_name in context:
            return parameter_name

        if not is_optional:
            raise RuntimeError('Unknown parameter: %s '
                               'for the step: %s '
                               'in the rule: %s '
                               'of the file: %s'
                               % (parameter_name, context['stack'], self.index, self.path))
        return None

    @staticmethod
    def _match_value(value: Any, condition: Any) -> bool:
        if isinstance(condition, list):
            for alternative in condition:
                if Rule._match_value(value, alternative):
                    return True
            return False

        if isinstance(condition, Pattern):
            return bool(condition.fullmatch(value))

        return value == condition

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

        if value is None:
            value = {}

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
            data = yaml.full_load(file.read())

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
    Global configuration class.
    """

    #: All the known configuration rules (including for per-rule parameters).
    rules: List[Rule]

    #: The directory where to create the per-rule configuration files.
    DIRECTORY: str

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Config.rules = []
        Config.DIRECTORY = os.getenv('DYNAMAKE_CONFIG_DIR', '.dynamake')

    @staticmethod
    def path_for_context(context: Dict[str, Any]) -> str:
        """
        Return the path name of a configuration file for a specific step invocation.

        The file name is based on a digest of the full invocation context.
        """
        digester = md5()
        digester.update(yaml.dump(context).encode('utf-8'))
        return os.path.join(Config.DIRECTORY, 'config.%s.yaml' % UUID(bytes=digester.digest()))

    @staticmethod
    def values_for_context(context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return the configuration values for a specific step invocation.
        """
        values: Dict[str, Any] = {}
        for rule in Config.rules:
            if rule.is_match(context):
                for name, value in rule.then.items():
                    if name.endswith('?'):
                        other_name = name[:-1]
                    else:
                        other_name = name + '?'
                    if other_name in values:
                        del values[other_name]
                    values[name] = value
        return values


Config.reset()
