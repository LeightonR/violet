from __future__ import annotations

import unittest

from violet.formatting import normalize_transcript


class TranscriptFormattingTests(unittest.TestCase):
    def test_repairs_spacing_around_existing_punctuation(self) -> None:
        self.assertEqual(
            normalize_transcript("  Hello ,  world !  ( ready )  "),
            "Hello,  world!  (ready)",
        )

    def test_preserves_space_separated_number_list(self) -> None:
        self.assertEqual(normalize_transcript("1, 2, 3"), "1, 2, 3")

    def test_does_not_join_space_separated_number_fragments(self) -> None:
        self.assertEqual(normalize_transcript("2025. 2026"), "2025. 2026")

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
