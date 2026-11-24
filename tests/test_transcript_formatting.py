from __future__ import annotations

import unittest

from violet.formatting import normalize_transcript


class TranscriptFormattingTests(unittest.TestCase):
    def test_repairs_spacing_around_existing_punctuation(self) -> None:
        self.assertEqual(
            normalize_transcript("  Hello ,  world !  ( ready )  "),
            "Hello,  world!  (ready)",
        )

    def test_repairs_spaced_ellipsis_and_numbers(self) -> None:
        self.assertEqual(
            normalize_transcript("Version 3 . 14 costs 1 , 000 . . ."),
            "Version 3.14 costs 1,000...",
        )

    def test_does_not_invent_spoken_punctuation(self) -> None:
        self.assertEqual(
            normalize_transcript("Write the word comma before period"),
            "Write the word comma before period",
        )

    def test_preserves_well_formatted_model_output(self) -> None:
        text = "Violet inserts accurate, punctuated text immediately."
        self.assertEqual(normalize_transcript(text), text)

    def test_blank_result_stays_blank(self) -> None:
        self.assertEqual(normalize_transcript(" \t\n "), "")


if __name__ == "__main__":
    unittest.main()
