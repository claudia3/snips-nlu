import io
import json
import os
import shutil
import unittest

from mock import MagicMock, patch, call

from snips_nlu.built_in_entities import BuiltInEntity
from snips_nlu.constants import MATCH_RANGE, VALUE, ENTITY
from snips_nlu.intent_classifier.snips_intent_classifier import \
    SnipsIntentClassifier
from snips_nlu.intent_parser.probabilistic_intent_parser import (
    augment_slots, spans_to_tokens_indexes, ProbabilisticIntentParser,
    DataAugmentationConfig)
from snips_nlu.languages import Language
from snips_nlu.result import ParsedSlot
from snips_nlu.slot_filler.crf_tagger import CRFTagger, default_crf_model
from snips_nlu.slot_filler.crf_utils import (BEGINNING_PREFIX, INSIDE_PREFIX,
                                             TaggingScheme)
from snips_nlu.tests.utils import BEVERAGE_DATASET, TEST_PATH
from snips_nlu.tokenization import Token, tokenize


class TestProbabilisticIntentParser(unittest.TestCase):
    def setUp(self):
        fixtures_directory = os.path.join(TEST_PATH, "fixtures",
                                          "probabilistic_parser")
        self.expected_parser_directory = os.path.join(fixtures_directory,
                                                      "expected_output")
        self.actual_parser_directory = os.path.join(fixtures_directory,
                                                    "actual_output")

    def tearDown(self):
        if os.path.isdir(self.actual_parser_directory):
            shutil.rmtree(self.actual_parser_directory)

    def test_spans_to_tokens_indexes(self):
        # Given
        spans = [
            (0, 1),
            (2, 6),
            (5, 6),
            (9, 15)
        ]
        tokens = [
            Token(value="abc", start=0, end=3, stem="abc"),
            Token(value="def", start=4, end=7, stem="def"),
            Token(value="ghi", start=10, end=13, stem="ghi")
        ]

        # When
        indexes = spans_to_tokens_indexes(spans, tokens)

        # Then
        expected_indexes = [[0], [0, 1], [1], [2]]
        self.assertListEqual(indexes, expected_indexes)

    def test_augment_slots(self):
        # Given
        text = "Find me a flight before 10pm and after 8pm"
        tokens = tokenize(text)
        intent_slots_mapping = {
            "start_date": "snips/datetime",
            "end_date": "snips/datetime",
        }
        missing_slots = {"start_date", "end_date"}
        builtin_entities = [
            {
                MATCH_RANGE: (16, 28),
                VALUE: " before 10pm",
                ENTITY: BuiltInEntity.DATETIME
            },
            {
                MATCH_RANGE: (33, 42),
                VALUE: "after 8pm",
                ENTITY: BuiltInEntity.DATETIME
            }
        ]

        tags = ['O' for _ in tokens]

        def mocked_sequence_probability(_tokens, _tags):
            first_tags = ['O' for _ in _tokens]
            first_tags[4] = '%sstart_date' % BEGINNING_PREFIX
            first_tags[5] = '%sstart_date' % INSIDE_PREFIX
            first_tags[7] = '%send_date' % BEGINNING_PREFIX
            first_tags[8] = '%send_date' % INSIDE_PREFIX

            second_tags = ['O' for _ in _tokens]
            second_tags[4] = '%send_date' % BEGINNING_PREFIX
            second_tags[5] = '%send_date' % INSIDE_PREFIX
            second_tags[7] = '%sstart_date' % BEGINNING_PREFIX
            second_tags[8] = '%sstart_date' % INSIDE_PREFIX

            if _tags == first_tags:
                return 0.6
            if _tags == second_tags:
                return 0.8
            else:
                raise ValueError("Unexpected tag sequence: %s" % _tags)

        tagger = MagicMock()
        tagger.get_sequence_probability = MagicMock(
            side_effect=mocked_sequence_probability)
        tagger.tagging_scheme = TaggingScheme.BIO

        # When
        augmented_slots = augment_slots(text, tokens, tags, tagger,
                                        intent_slots_mapping,
                                        builtin_entities, missing_slots)

        # Then
        expected_slots = [
            ParsedSlot(value='before 10pm', match_range=(17, 28),
                       entity='snips/datetime', slot_name='end_date'),
            ParsedSlot(value='after 8pm', match_range=(33, 42),
                       entity='snips/datetime', slot_name='start_date')
        ]
        self.assertListEqual(augmented_slots, expected_slots)

    @patch('snips_nlu.slot_filler.crf_tagger.CRFTagger.fit')
    @patch('snips_nlu.slot_filler.crf_tagger.CRFTagger.save')
    @patch('snips_nlu.intent_parser.probabilistic_intent_parser'
           '.SnipsIntentClassifier.to_dict')
    def test_should_be_saveable(self, mock_classifier_to_dict,
                                mock_tagger_save, mock_tagger_fit):
        # Given
        language = Language.EN
        mock_classifier_to_dict.return_value = {
            "mocked_dict_key": "mocked_dict_value"}
        intent_classifier = SnipsIntentClassifier(language)
        slot_name_to_entity_mapping = {
            "number_of_cups": "snips/number",
            "beverage_temperature": "Temperature"
        }

        features_signatures = [
            {
                "factory_name": "get_shape_ngram_fn",
                "args": {"n": 1},
                "offsets": [0]
            },
            {
                "factory_name": "get_shape_ngram_fn",
                "args": {"n": 2},
                "offsets": [-1, 0]
            }
        ]

        tagging_scheme = TaggingScheme.BIO

        taggers_directory = os.path.join(self.actual_parser_directory,
                                         "taggers")
        make_coffee_crf = default_crf_model(
            os.path.join(taggers_directory, "MakeCoffee", "model.crfsuite"))
        make_tea_crf = default_crf_model(
            os.path.join(taggers_directory, "MakeTea", "model.crfsuite"))
        make_coffee_tagger = CRFTagger(make_coffee_crf, features_signatures,
                                       tagging_scheme, language)
        make_tea_tagger = CRFTagger(make_tea_crf, features_signatures,
                                    tagging_scheme, language)
        taggers = {
            "MakeCoffee": make_coffee_tagger,
            "MakeTea": make_tea_tagger,
        }

        mock_tagger_fit.side_effect = [make_coffee_tagger, make_tea_tagger]

        parser = ProbabilisticIntentParser(language, intent_classifier,
                                           taggers,
                                           slot_name_to_entity_mapping, None)
        parser.fit(BEVERAGE_DATASET)

        # When
        parser.save(self.actual_parser_directory)

        # Then
        make_coffee_dir = os.path.join(taggers_directory, "MakeCoffee")
        make_tea_dir = os.path.join(taggers_directory, "MakeTea")
        calls = [call(make_coffee_dir), call(make_tea_dir)]
        mock_tagger_save.assert_has_calls(calls)
        expected_config_path = os.path.join(self.expected_parser_directory,
                                            "probabilistic_parser_config.json")
        with io.open(expected_config_path) as f:
            expected_config = json.load(f)

        actual_config_path = os.path.join(self.actual_parser_directory,
                                          "probabilistic_parser_config.json")
        with io.open(actual_config_path) as f:
            actual_config = json.load(f)

        self.assertDictEqual(actual_config, expected_config)

        taggers_dir_exist = os.path.isdir(taggers_directory)
        self.assertTrue(taggers_dir_exist)

        if taggers_dir_exist:
            intents_dirs = os.listdir(taggers_directory)
            self.assertListEqual(intents_dirs, ["MakeCoffee", "MakeTea"])

    @patch('snips_nlu.intent_parser.probabilistic_intent_parser'
           '.SnipsIntentClassifier.from_dict')
    @patch('snips_nlu.intent_parser.probabilistic_intent_parser'
           '.CRFTagger')
    def test_should_be_loadable(self, mock_tagger, mock_classifier_from_dict):
        # When
        language = Language.EN
        mocked_tagger = MagicMock()
        mock_tagger.load.return_value = mocked_tagger
        mocked_tagger.language = language
        parser = ProbabilisticIntentParser.load(self.expected_parser_directory)

        # Then
        mock_classifier_from_dict.assert_called_once_with(
            {"mocked_dict_key": "mocked_dict_value"})
        taggers_directory = os.path.join(self.expected_parser_directory,
                                         "taggers")
        make_coffee_dir = os.path.join(taggers_directory, "MakeCoffee")
        make_tea_dir = os.path.join(taggers_directory, "MakeTea")
        calls = [call(make_coffee_dir), call(make_tea_dir)]
        mock_tagger.load.assert_has_calls(calls)

        expected_slot_name_to_entity_mapping = {
            "beverage_temperature": "Temperature",
            "number_of_cups": "snips/number"
        }

        expected_data_augmentation_config = DataAugmentationConfig.from_dict(
            {
                "max_utterances": 200,
                "noise_prob": 0.0,
                "min_noise_size": 0,
                "max_noise_size": 0
            }
        )

        self.assertEqual(parser.language, language)
        self.assertEqual(parser.slot_name_to_entity_mapping,
                         expected_slot_name_to_entity_mapping)
        self.assertEqual(parser.data_augmentation_config,
                         expected_data_augmentation_config)
        self.assertIsNotNone(parser.intent_classifier)
        self.assertListEqual(parser.crf_taggers.keys(),
                             ["MakeCoffee", "MakeTea"])
