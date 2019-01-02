"""
Test the configuration classes.
"""

from dynamake.config import Config
from tests import TestWithFiles
from tests import write_file

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use


class TestConfig(TestWithFiles):

    def test_load_missing(self) -> None:
        self.assertRaisesRegex(FileNotFoundError,  # type: ignore
                               "No such file.*: 'missing.yaml'",
                               Config.load, 'missing.yaml')

    def test_non_top_list(self) -> None:
        write_file('non_top_list.yaml', '{}\n')
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'file: non_top_list.yaml .* top-level sequence',
                               Config.load, 'non_top_list.yaml')

    def test_non_mapping_entry(self) -> None:
        write_file('non_mapping_entry.yaml', '- []\n')
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'mapping rule: 0 .* file: non_mapping_entry.yaml',
                               Config.load, 'non_mapping_entry.yaml')

    def test_missing_when(self) -> None:
        write_file('missing_when.yaml', '- {then: {}}\n')
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'key: when .* rule: 0 .* file: missing_when.yaml',
                               Config.load, 'missing_when.yaml')

    def test_non_dict_when(self) -> None:
        write_file('non_dict_when.yaml', '- {when: [], then: {}}\n')
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'mapping .* key: when .* rule: 0 .* file: non_dict_when.yaml',
                               Config.load, 'non_dict_when.yaml')

    def test_non_string_sub_key(self) -> None:
        write_file('non_string.yaml', '- {when: {1: a}, then: {}}\n')
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'string: 1 .* key: when .* rule: 0 .* file: non_string.yaml',
                               Config.load, 'non_string.yaml')

    def test_unknown_key(self) -> None:
        write_file('unknown_key.yaml', '- {when: {}, then: {}, else: {}}\n')
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'key: else .* rule: 0 .* file: unknown_key.yaml',
                               Config.load, 'unknown_key.yaml')

    def test_unknown_parameter(self) -> None:
        write_file('unknown_parameter.yaml', '- {when: {step: foo, bar: 1}, then: {}}\n')
        Config.load('unknown_parameter.yaml')
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'parameter: bar .* step: foo .* '
                               'rule: 0 .* file: unknown_parameter.yaml',
                               Config.values_for_step, ['foo'], {'step': 'foo'})

    def test_invalid_pattern(self) -> None:
        write_file('invalid_pattern.yaml', '- {when: {context: 1}, then: {}}\n')
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'Invalid pattern.* rule: 0 .* file: invalid_pattern.yaml',
                               Config.load, 'invalid_pattern.yaml')

    def test_load_empty(self) -> None:
        write_file('empty.yaml', '')
        Config.load('empty.yaml')
        self.assertEqual(Config.rules, [])
        self.assertEqual(Config.values_for_step(['foo'], {'a': 1}), {})

    def test_last_one_wins(self) -> None:
        write_file('config.yaml', """
            - when: {a: 1}
              then: {p: 1, q: 1}

            - when: {b: [2, 3]}
              then: {p: 2, r: 2}

            - when: {step: [bar]}
              then: {a: 1}

            - when: {context: [+.bar]}
              then: {a: 2}

            - when: {context: baz.bar}
              then: {a: 3}
        """)
        Config.load('config.yaml')

        self.assertEqual(Config.values_for_step(['foo'], {'a': 1}),
                         {'p': 1, 'q': 1})

        self.assertEqual(Config.values_for_step(['foo'], {'b': 2}),
                         {'p': 2, 'r': 2})

        self.assertEqual(Config.values_for_step(['foo'], {'a': 1, 'b': 2}),
                         {'p': 2, 'q': 1, 'r': 2})

        self.assertEqual(Config.values_for_step(['foo'], {'c': 3}), {})

        self.assertEqual(Config.values_for_step(['bar'], {}), {'a': 1})
        self.assertEqual(Config.values_for_step(['baz', 'bar'], {}), {'a': 3})
        self.assertEqual(Config.values_for_step(['vaz', 'bar'], {}), {'a': 2})
        self.assertEqual(Config.values_for_step(['baz'], {}), {})

    def test_lambda(self) -> None:
        write_file('config.yaml', """
            - when: {}
              then: {p: 0}
            - when: {lambda a: a > 1}
              then: {p: 1}
        """)
        Config.load('config.yaml')

        self.assertEqual(Config.values_for_step(['foo'], {'a': 1}), {'p': 0})

        self.assertEqual(Config.values_for_step(['foo'], {'a': 2}), {'p': 1})

        self.assertEqual(Config.values_for_step(['foo'], {'b': 1}), {'p': 0})

    def test_path_for_step(self) -> None:
        minimal_path = Config.path_for_step(['foo'], {})
        nested_path = Config.path_for_step(['bar', 'foo'], {})
        arg_path = Config.path_for_step(['foo'], {'a': 1})

        self.assertNotEqual(minimal_path, nested_path)
        self.assertNotEqual(minimal_path, arg_path)
        self.assertNotEqual(nested_path, arg_path)
