"""
Test the configuration classes.
"""

from dynamake.config import Config
from tests import TestWithFiles
from tests import write_file

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use


class TestConfig(TestWithFiles):

    def test_load_missing(self) -> None:
        self.assertRaisesRegex(FileNotFoundError,
                               "No such file.*: 'missing.yaml'",
                               Config.load, 'missing.yaml')

    def test_non_top_list(self) -> None:
        write_file('non_top_list.yaml', '{}\n')
        self.assertRaisesRegex(RuntimeError,
                               'file: non_top_list.yaml .* top-level sequence',
                               Config.load, 'non_top_list.yaml')

    def test_non_mapping_entry(self) -> None:
        write_file('non_mapping_entry.yaml', '- []\n')
        self.assertRaisesRegex(RuntimeError,
                               'mapping rule: 0 .* file: non_mapping_entry.yaml',
                               Config.load, 'non_mapping_entry.yaml')

    def test_missing_when(self) -> None:
        write_file('missing_when.yaml', '- {then: {}}\n')
        self.assertRaisesRegex(RuntimeError,
                               'key: when .* rule: 0 .* file: missing_when.yaml',
                               Config.load, 'missing_when.yaml')

    def test_non_dict_when(self) -> None:
        write_file('non_dict_when.yaml', '- {when: [], then: {}}\n')
        self.assertRaisesRegex(RuntimeError,
                               'mapping .* key: when .* rule: 0 .* file: non_dict_when.yaml',
                               Config.load, 'non_dict_when.yaml')

    def test_non_string_sub_key(self) -> None:
        write_file('non_string.yaml', '- {when: {1: a}, then: {}}\n')
        self.assertRaisesRegex(RuntimeError,
                               'string: 1 .* key: when .* rule: 0 .* file: non_string.yaml',
                               Config.load, 'non_string.yaml')

    def test_unknown_key(self) -> None:
        write_file('unknown_key.yaml', '- {when: {}, then: {}, else: {}}\n')
        self.assertRaisesRegex(RuntimeError,
                               'key: else .* rule: 0 .* file: unknown_key.yaml',
                               Config.load, 'unknown_key.yaml')

    def test_unknown_parameter(self) -> None:
        write_file('unknown_parameter.yaml', '- {when: {step: foo, bar: 1}, then: {}}\n')
        Config.load('unknown_parameter.yaml')
        self.assertRaisesRegex(RuntimeError,
                               'parameter: bar .* step: foo .* '
                               'rule: 0 .* file: unknown_parameter.yaml',
                               Config.values_for_context, {'step': 'foo'})

    def test_load_empty(self) -> None:
        write_file('empty.yaml', '')
        Config.load('empty.yaml')
        self.assertEqual(Config.rules, [])
        self.assertEqual(Config.values_for_context({'a': 1}), {})

    def test_last_one_wins(self) -> None:
        write_file('config.yaml', """
            - when: {}
              then: {'p?': 2, q: 2}

            - when: {a: 1}
              then: {p: 1}
        """)
        Config.load('config.yaml')

        self.assertEqual(Config.values_for_context({'a': 1}), {'p': 1, 'q': 2})

        self.assertEqual(Config.values_for_context({'a': 0}), {'p?': 2, 'q': 2})

    def test_match_step(self) -> None:
        write_file('config.yaml', """
            - when: {step: foo}
              then: {p: 1}
        """)

        Config.load('config.yaml')

        self.assertEqual(Config.values_for_context({'step': 'bar'}), {})
        self.assertEqual(Config.values_for_context({'step': 'foo'}), {'p': 1})

    def test_match_value(self) -> None:
        write_file('config.yaml', """
            - when: {a: 1}
              then: {p: 1}
        """)

        Config.load('config.yaml')

        self.assertEqual(Config.values_for_context({'a': 0}), {})
        self.assertEqual(Config.values_for_context({'a': 1}), {'p': 1})

        self.assertRaisesRegex(RuntimeError,
                               'parameter: a .* step: foo .* file: config.yaml',
                               Config.values_for_context, {'step': 'foo'})

    def test_match_optional_value(self) -> None:
        write_file('config.yaml', """
            - when: {'a?': 1}
              then: {p: 1}
        """)

        Config.load('config.yaml')

        self.assertEqual(Config.values_for_context({'a': 0}), {})
        self.assertEqual(Config.values_for_context({'a': 1}), {'p': 1})

        self.assertEqual(Config.values_for_context({}), {})

    def test_match_list(self) -> None:
        write_file('config.yaml', """
            - when: {a: [2, 3]}
              then: {p: 1}
        """)

        Config.load('config.yaml')

        self.assertEqual(Config.values_for_context({'a': 1}), {})
        self.assertEqual(Config.values_for_context({'a': 2}), {'p': 1})

    def test_match_regexp(self) -> None:
        write_file('config.yaml', """
            - when: {a: !r 'a.*b'}
              then: {p: 1}
        """)

        Config.load('config.yaml')

        self.assertEqual(Config.values_for_context({'a': 'a'}), {})
        self.assertEqual(Config.values_for_context({'a': 'ab'}), {'p': 1})
        self.assertEqual(Config.values_for_context({'a': 'acb'}), {'p': 1})

    def test_match_glob(self) -> None:
        write_file('config.yaml', """
            - when: {a: !g 'a?b'}
              then: {p: 1}
        """)

        Config.load('config.yaml')

        self.assertEqual(Config.values_for_context({'a': 'b'}), {})
        self.assertEqual(Config.values_for_context({'a': 'ab'}), {})
        self.assertEqual(Config.values_for_context({'a': 'acb'}), {'p': 1})

    def test_match_lambda(self) -> None:
        write_file('config.yaml', """
            - when:
                lambda a: a > 1
              then: {p: 1}
        """)
        Config.load('config.yaml')

        self.assertEqual(Config.values_for_context({'a': 1}), {})

        self.assertEqual(Config.values_for_context({'a': 2}), {'p': 1})

        self.assertRaisesRegex(RuntimeError,
                               'parameter: a .* step: foo .* file: config.yaml',
                               Config.values_for_context, {'step': 'foo'})

    def test_match_optional_lambda(self) -> None:
        write_file('config.yaml', """
            - when:
                lambda a?: a > 1
              then: {p: 1}
        """)
        Config.load('config.yaml')

        self.assertEqual(Config.values_for_context({'a': 1}), {})

        self.assertEqual(Config.values_for_context({'a': 2}), {'p': 1})

        self.assertEqual(Config.values_for_context({}), {})
