#!/usr/bin/env python3
"""Regression checks for invalid or inconsistent civic registry records."""
import copy
import unittest
from validate_package import ROOT, read_json, validate_case, validate_package


class RegistryChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = read_json(ROOT / 'assets/case-schema.json')
        cls.fixture = read_json(ROOT / 'evals/fixtures/registry-valid.json')

    def setUp(self):
        self.data = copy.deepcopy(self.fixture)

    def rejects(self):
        with self.assertRaises(ValueError):
            validate_case(self.data, self.schema)

    def test_package(self):
        self.assertGreaterEqual(validate_package(), 30)

    def test_empty_template(self):
        self.assertTrue(validate_case(read_json(ROOT / 'assets/case-template.json'), self.schema))

    def test_complete_synthetic_record(self):
        self.assertTrue(validate_case(self.data, self.schema))

    def test_duplicate_case_ids(self):
        self.data['cases'].append(copy.deepcopy(self.data['cases'][0]))
        self.rejects()

    def test_missing_source(self):
        self.data['claims'][0]['source_ids'] = ['missing']
        self.rejects()

    def test_claim_cannot_be_supported_by_unread_material(self):
        self.data['sources'][0]['availability'] = 'referenced_only'
        self.rejects()

    def test_registration_needs_source(self):
        self.data['threads'][0]['registration_source_ids'] = []
        self.rejects()

    def test_registration_needs_date(self):
        self.data['threads'][0]['registered_on'] = None
        self.rejects()

    def test_unknown_deadline_has_no_fabricated_date(self):
        self.data['threads'][0]['deadline']['kind'] = 'unknown'
        self.rejects()

    def test_legal_deadline_needs_reviewed_basis(self):
        self.data['threads'][0]['deadline']['kind'] = 'legal'
        self.rejects()

    def test_invalid_calendar_date(self):
        self.data['actions'][0]['due_on'] = '2026-02-30'
        self.rejects()

    def test_event_cannot_silently_merge_cases(self):
        second = copy.deepcopy(self.data['cases'][0])
        second['id'] = 'second-case'
        self.data['cases'].append(second)
        self.data['events'][0]['case_id'] = 'second-case'
        self.rejects()

    def test_attribution_needs_its_own_sources(self):
        self.data['outcomes'][0]['attribution'] = 'supported'
        self.rejects()

    def test_unknown_owner_is_allowed(self):
        self.data['actions'][0]['owner_actor_id'] = None
        self.assertTrue(validate_case(self.data, self.schema))

    def test_unknown_owner_must_not_be_invented_reference(self):
        self.data['actions'][0]['owner_actor_id'] = 'nonexistent-person'
        self.rejects()

    def test_negative_time_is_invalid(self):
        self.data['actions'][0]['hours_estimate'] = -1
        self.rejects()

    def test_legacy_codes_are_rejected(self):
        self.data['claims'][0]['epistemic_status'] = 'D'
        self.rejects()

    def test_undeclared_fields_are_rejected(self):
        self.data['sources'][0]['automatically_redacted'] = True
        self.rejects()


class CurrentRegistryChecks(unittest.TestCase):
    def setUp(self):
        self.schema = read_json(ROOT / 'assets/case-schema.json')
        self.data = read_json(ROOT / 'evals/fixtures/registry-v0.5-valid.json')

    def rejects(self):
        with self.assertRaises(ValueError):
            validate_case(self.data, self.schema)

    def test_current_fixture(self):
        self.assertTrue(validate_case(self.data, self.schema))

    def test_unknown_status_and_freshness(self):
        self.data['cases'][0]['status'] = 'unknown'
        self.data['cases'][0]['last_verified_on'] = None
        self.assertTrue(validate_case(self.data, self.schema))

    def test_freshness_is_per_case(self):
        second = copy.deepcopy(self.data['cases'][0])
        second['id'] = 'second-case'
        second['last_verified_on'] = '2026-10-01'
        self.data['cases'].append(second)
        self.data['cases'][0]['last_verified_on'] = '2026-11-10'
        self.assertTrue(validate_case(self.data, self.schema))
        self.data['cases'][1]['last_verified_on'] = '2026-02-30'
        self.rejects()

    def test_missing_applicant_reference(self):
        self.data['cases'][0]['applicant_actor_ids'] = ['missing']
        self.rejects()

    def test_current_case_requires_explicit_metadata(self):
        del self.data['cases'][0]['applicant_actor_ids']
        self.rejects()

    def test_case_commitment_requires_case(self):
        self.data['commitments'][0]['case_id'] = None
        self.data['commitments'][0]['thread_id'] = None
        self.rejects()

    def test_commitment_cannot_point_to_other_case_thread(self):
        second = copy.deepcopy(self.data['cases'][0])
        second['id'] = 'second-case'
        self.data['cases'].append(second)
        self.data['commitments'][0]['case_id'] = 'second-case'
        self.rejects()

    def test_unknown_commitment_scope_is_explicit(self):
        self.data['commitments'][0].update(scope='unknown', case_id=None, thread_id=None)
        self.assertTrue(validate_case(self.data, self.schema))

    def test_campaign_commitment_has_no_case_link(self):
        self.data['commitments'][0]['scope'] = 'campaign'
        self.rejects()
        self.data['commitments'][0].update(case_id=None, thread_id=None)
        self.assertTrue(validate_case(self.data, self.schema))

    def test_same_file_cannot_be_two_sources(self):
        self.data['sources'][0]['content_sha256'] = 'a' * 64
        duplicate = copy.deepcopy(self.data['sources'][0])
        duplicate['id'] = 'duplicate-file'
        duplicate['locator'] = 'Renamed copy'
        self.data['sources'].append(duplicate)
        self.rejects()

    def test_reply_reimport_cannot_duplicate_event(self):
        self.data['events'][0]['event_type'] = 'replied'
        self.data['events'][0]['reply_source_id'] = 'demo-source'
        duplicate = copy.deepcopy(self.data['events'][0])
        duplicate['id'] = 'duplicate-reply'
        self.data['events'].append(duplicate)
        self.rejects()

    def test_one_document_can_reply_to_two_threads(self):
        self.data['events'][0]['event_type'] = 'replied'
        self.data['events'][0]['reply_source_id'] = 'demo-source'
        second = copy.deepcopy(self.data['threads'][0])
        second.update(id='second-thread', next_action_id=None)
        self.data['threads'].append(second)
        event = copy.deepcopy(self.data['events'][0])
        event.update(id='second-reply', thread_id='second-thread')
        self.data['events'].append(event)
        self.assertTrue(validate_case(self.data, self.schema))

    def test_different_replies_can_share_supporting_sources(self):
        first_source = copy.deepcopy(self.data['sources'][0])
        first_source['id'] = 'reply-one'
        second_source = copy.deepcopy(first_source)
        second_source['id'] = 'reply-two'
        self.data['sources'].extend([first_source, second_source])
        first = copy.deepcopy(self.data['events'][0])
        first.update(id='reply-one', event_type='replied', reply_source_id='reply-one', source_ids=['reply-one', 'demo-source'])
        second = copy.deepcopy(first)
        second.update(id='reply-two', reply_source_id='reply-two', source_ids=['reply-two', 'demo-source'])
        self.data['events'].extend([first, second])
        self.assertTrue(validate_case(self.data, self.schema))

    def test_reply_requires_primary_source(self):
        self.data['events'][0]['event_type'] = 'replied'
        self.rejects()

    def test_next_action_must_match_thread(self):
        second = copy.deepcopy(self.data['threads'][0])
        second['id'] = 'second-thread'
        self.data['threads'].append(second)
        self.rejects()


if __name__ == '__main__':
    unittest.main(verbosity=2)
