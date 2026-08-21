"""
These tests run with no ANTHROPIC_API_KEY configured (the default test
environment), which exercises the exact failure path the spec asks about:
"LLM failures must be handled gracefully, system should not break."
No network call, no mocking needed — the missing-key path IS a real failure
mode (e.g. a misconfigured deploy), so testing it directly is honest and
still valuable.
"""

from django.test import SimpleTestCase, override_settings
from .services import generate_pre_visit_summary, generate_post_visit_summary


class LLMFailureHandlingTests(SimpleTestCase):

    @override_settings(ANTHROPIC_API_KEY='')
    def test_pre_visit_summary_degrades_gracefully_without_key(self):
        result = generate_pre_visit_summary("fever and headache for 2 days")
        self.assertTrue(result['failed'])
        # Must default to Medium, never Low, so a real urgent case can't
        # silently look calm just because the AI call couldn't run.
        self.assertEqual(result['urgency_level'], 'Medium')
        self.assertIsInstance(result['chief_complaint'], str)
        self.assertEqual(result['suggested_questions'], [])

    @override_settings(ANTHROPIC_API_KEY='')
    def test_post_visit_summary_degrades_gracefully_without_key(self):
        result = generate_post_visit_summary("Viral infection, rest advised.", "Paracetamol 500mg 2x/day")
        self.assertTrue(result['failed'])
        self.assertIn('could not be generated', result['summary'])

    @override_settings(ANTHROPIC_API_KEY='')
    def test_pre_visit_never_raises(self):
        # The whole point: this must not throw, no matter what.
        try:
            generate_pre_visit_summary("")
            generate_pre_visit_summary("x" * 5000)
        except Exception as e:
            self.fail(f"generate_pre_visit_summary raised unexpectedly: {e}")
