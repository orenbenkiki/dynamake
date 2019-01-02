"""
Test the pattern matching.
"""


from unittest import TestCase

from dynamake.patterns import Pattern

# pylint: disable=missing-docstring


class TestPatterns(TestCase):

    def test_zero_or_more(self) -> None:
        self.assertTrue(Pattern('*').is_match([]))
        self.assertTrue(Pattern('*').is_match(['a']))
        self.assertTrue(Pattern('*').is_match(['a', 'b']))

        self.assertTrue(Pattern('*.*').is_match([]))
        self.assertTrue(Pattern('*.*').is_match(['a']))
        self.assertTrue(Pattern('*.*').is_match(['a', 'b']))

        self.assertFalse(Pattern('*.+').is_match([]))
        self.assertTrue(Pattern('*.+').is_match(['a']))
        self.assertTrue(Pattern('*.+').is_match(['a', 'b']))

        self.assertFalse(Pattern('*.?').is_match([]))
        self.assertTrue(Pattern('*.?').is_match(['a']))
        self.assertTrue(Pattern('*.?').is_match(['a', 'b']))

        self.assertFalse(Pattern('*.a').is_match([]))
        self.assertTrue(Pattern('*.a').is_match(['a']))
        self.assertFalse(Pattern('*.a').is_match(['a', 'b']))

        self.assertFalse(Pattern('*.b').is_match([]))
        self.assertFalse(Pattern('*.b').is_match(['a']))
        self.assertTrue(Pattern('*.b').is_match(['a', 'b']))

    def test_one_or_more(self) -> None:
        self.assertFalse(Pattern('+').is_match([]))
        self.assertTrue(Pattern('+').is_match(['a']))
        self.assertTrue(Pattern('+').is_match(['a', 'b']))

        self.assertFalse(Pattern('+.*').is_match([]))
        self.assertTrue(Pattern('+.*').is_match(['a']))
        self.assertTrue(Pattern('+.*').is_match(['a', 'b']))

        self.assertFalse(Pattern('+.+').is_match([]))
        self.assertFalse(Pattern('+.+').is_match(['a']))
        self.assertTrue(Pattern('+.+').is_match(['a', 'b']))

        self.assertFalse(Pattern('+.?').is_match([]))
        self.assertFalse(Pattern('+.?').is_match(['a']))
        self.assertTrue(Pattern('+.?').is_match(['a', 'b']))

        self.assertFalse(Pattern('+.a').is_match([]))
        self.assertFalse(Pattern('+.a').is_match(['a']))
        self.assertFalse(Pattern('+.a').is_match(['a', 'b']))

        self.assertFalse(Pattern('+.b').is_match([]))
        self.assertFalse(Pattern('+.b').is_match(['a']))
        self.assertTrue(Pattern('+.b').is_match(['a', 'b']))

    def test_zero_or_one(self) -> None:
        self.assertFalse(Pattern('?').is_match([]))
        self.assertTrue(Pattern('?').is_match(['a']))
        self.assertFalse(Pattern('?').is_match(['a', 'b']))

        self.assertFalse(Pattern('?.*').is_match([]))
        self.assertTrue(Pattern('?.*').is_match(['a']))
        self.assertTrue(Pattern('?.*').is_match(['a', 'b']))

        self.assertFalse(Pattern('?.+').is_match([]))
        self.assertFalse(Pattern('?.+').is_match(['a']))
        self.assertTrue(Pattern('?.+').is_match(['a', 'b']))

        self.assertFalse(Pattern('?.?').is_match([]))
        self.assertFalse(Pattern('?.?').is_match(['a']))
        self.assertTrue(Pattern('?.?').is_match(['a', 'b']))

        self.assertFalse(Pattern('?.a').is_match([]))
        self.assertFalse(Pattern('?.a').is_match(['a']))
        self.assertFalse(Pattern('?.a').is_match(['a', 'b']))

        self.assertFalse(Pattern('?.b').is_match([]))
        self.assertFalse(Pattern('?.b').is_match(['a']))
        self.assertTrue(Pattern('?.b').is_match(['a', 'b']))

    def test_literal(self) -> None:
        self.assertFalse(Pattern('a').is_match([]))
        self.assertTrue(Pattern('a').is_match(['a']))
        self.assertFalse(Pattern('a').is_match(['a', 'b']))

        self.assertFalse(Pattern('a.+').is_match([]))
        self.assertFalse(Pattern('a.+').is_match(['a']))
        self.assertTrue(Pattern('a.+').is_match(['a', 'b']))

        self.assertFalse(Pattern('a.?').is_match([]))
        self.assertFalse(Pattern('a.?').is_match(['a']))
        self.assertTrue(Pattern('a.?').is_match(['a', 'b']))

        self.assertFalse(Pattern('a.*').is_match([]))
        self.assertTrue(Pattern('a.*').is_match(['a']))
        self.assertTrue(Pattern('a.*').is_match(['a', 'b']))

        self.assertFalse(Pattern('a.a').is_match([]))
        self.assertFalse(Pattern('a.a').is_match(['a']))
        self.assertFalse(Pattern('a.a').is_match(['a', 'b']))

        self.assertFalse(Pattern('a.b').is_match([]))
        self.assertFalse(Pattern('a.b').is_match(['a']))
        self.assertTrue(Pattern('a.b').is_match(['a', 'b']))
