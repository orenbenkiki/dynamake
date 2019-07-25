"""
Manage per-step configurations.
"""

from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union
from typing.re import Pattern  # type: ignore # pylint: disable=import-error

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

    _BUILTINS = ['step', 'context']

    def is_match(self, context: Dict[str, Any]) -> bool:
        """
        Whether the rule is a match for a step invocation.
        """
        return self._match_builtins(context) \
            and self._verify_known_parameters(context) \
            and self._match_other_conditions(context)

    def _match_builtins(self, context: Dict[str, Any]) -> bool:
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
        if key == 'context':
            return Rule._match_context(condition, context)

        if key.startswith('lambda '):
            return self._match_lambda(key[7:],  # pylint: disable=protected-access
                                      condition, context)

        parameter_name = self._parameter_name(key, context)
        return parameter_name is not None and Rule._match_value(context[parameter_name], condition)

    @staticmethod
    def _match_context(required_parameters: Union[str, List[str]], context: Dict[str, Any]) -> bool:
        if isinstance(required_parameters, str):
            required_parameters = [required_parameters]
        for parameter in required_parameters:
            if parameter not in context:
                return False
        return True

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
                               % (parameter_name, context['step'], self.index, self.path))
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

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Config.rules = []

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

    @staticmethod
    def load(path: str) -> None:
        """
        Load a YAML configuration file into :py:attr:`dynamake.config.Config.rules`.

        The rules from the loaded files will override any values specified by previously loaded
        rules. In general, the last matching rule wins.

        The configuration YAML file should be empty, or contain a top-level sequence.
        Each entry must be a mapping with two keys: ``when`` and ``then``.
        The ``then`` value should be a mapping from parameter names to their values.
        The ``when`` value should be a mapping of conditions.

        The condition key can be either a parameter name, or ``lambda parameter_name, ...``.
        If the key starts with ``lambda``, the value should be a string containing a
        python boolean expression, which will be evaluated using the specified parameters.

        Otherwise, the condition value should be one of:

        * The exact value of the parameter.

        * A ``!r regexp-pattern`` or a ``!g glob-pattern`` to match the (string) parameter value
          against. While general regular expressions are more powerful, glob patterns are simpler
          and more familiar to most people, as they are used by the shell.

        * A list of alternative values or patterns to match the parameter value against.

        A rule is applicable to a step invocation if all the conditions in its ``when`` mapping
        match the parameters used in the invocation.

        Two additional parameters are also added for the purpose of the matching: ``step`` contains
        the step function name, and the call ``stack`` contains the path of step names
        ``/top/.../step``. This uses ``/`` to separate the step names to allow matching it against a
        glob pattern, as if it was a file path. Conditions on these parameters are evaluated first,
        and if they reject the invocation, no other condition is evaluated.

        It is normally an error for the rest of the conditions to specify a parameter which is not
        one of the arguments of the invoked step function. However, if the condition key ends with
        ``?``, then the condition will silently reject the step instead. This allows specifying
        rules that provide defaults without worrying about applying the rule to the exact list of
        steps that use it.

        Default rules are very useful when used responsibly. However, they have two important
        downsides:

        * Modifying the values in such default rules will trigger the re-computation of all the
          action steps that match them, even if they do not actually depend on the provided values.

        * Default rules silently ignore typos in parameter names.

        It is pretty annoying to modify the configuration file, to adjust some parameter, re-execute
        the full computation, wait a few hours, and then discover this was wasted effort because you
        wrote ``foo: 1`` instead of ``foos: 1`` in the default rule.

        It is therefore recommended to minimize the use of default rules, and when they are used, to
        restrict them to match as few steps as possible, typically by matching against the steps
        call ``stack``.
        """
        Config.rules += Rule.load(path)


Config.reset()
