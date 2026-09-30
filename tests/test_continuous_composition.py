import unittest
from src.image_generator import _build_prompt
from src.story import SLOT_SCENES, BEAT_SCENES
from src.goc import GOC_IMAGE_SLOTS


class ContinuousCompositionTest(unittest.TestCase):
    def test_all_production_and_pilot_prompt_combinations(self):
        for track, scenes in [('EDT', SLOT_SCENES), ('GOC', GOC_IMAGE_SLOTS)]:
            for mode in ('full', 'production-video-pilot', 'budget-pilot'):
                selected = scenes[:1] if mode == 'budget-pilot' else scenes
                for reference in (False, True):
                    for slot, scene in enumerate(selected):
                        with self.subTest(track=track, mode=mode, reference=reference, slot=slot):
                            prompt = _build_prompt({'track': track, 'villain': 'Debt Titan'}, scene, reference)
                            for phrase in ('BOTTOM THIRD', 'UPPER TWO THIRDS', 'lower caption area'):
                                self.assertNotIn(phrase.lower(), prompt.lower())
                            self.assertIn('one continuous scene', prompt)
                            self.assertIn('No split panels', prompt)
                            self.assertIn('NO numbers', prompt)

    def test_storyboard_does_not_restore_split_instruction(self):
        for _, scene in BEAT_SCENES:
            self.assertNotIn('BOTTOM THIRD', scene)
            self.assertNotIn('UPPER TWO THIRDS', scene)
