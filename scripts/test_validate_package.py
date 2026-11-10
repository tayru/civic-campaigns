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


if __name__ == '__main__':
    unittest.main(verbosity=2)
