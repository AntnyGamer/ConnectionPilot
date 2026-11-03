import unittest
from security import is_trusted_linkedin_page, safe_diagnostic

class SecurityTests(unittest.TestCase):
    def test_strict_https_host(self):
        self.assertTrue(is_trusted_linkedin_page('https://www.linkedin.com/in/alice/'))
        self.assertTrue(is_trusted_linkedin_page('https://linkedin.com/feed/'))
        for value in ('https://www.linkedin.com.evil.example/in/a/',
                      'http://www.linkedin.com/feed/', 'https://evil.example/',
                      'https://user:pass@linkedin.com/feed',
                      'https://linkedin.com:444/feed', 'about:blank',
                      'https://accounts.google.com/'):
            with self.subTest(value=value):
                self.assertFalse(is_trusted_linkedin_page(value))

    def test_empty_exception_message_is_safe_to_report(self):
        self.assertEqual(safe_diagnostic(TimeoutError()), 'TimeoutError')
        self.assertEqual(safe_diagnostic(RuntimeError('')), 'RuntimeError')
        self.assertEqual(safe_diagnostic(ValueError('  ')), 'ValueError')

    def test_redact_browser_failure(self):
        err = RuntimeError('goto https://example.com/?access_token=secret123\nsecret second line')
        self.assertNotIn('secret123', safe_diagnostic(err))
        self.assertNotIn('second line', safe_diagnostic(err))
        self.assertIn('[URL redacted]', safe_diagnostic(err))
