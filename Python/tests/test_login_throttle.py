import unittest

from app.services import LoginLockedError, LoginThrottle


class LoginThrottleTest(unittest.TestCase):
    def setUp(self):
        LoginThrottle._attempts = {}

    def test_check_passes_with_no_history(self):
        LoginThrottle.check("alice")  # should not raise

    def test_locks_after_max_failures(self):
        for _ in range(LoginThrottle.MAX_FAILED_ATTEMPTS):
            LoginThrottle.record_failure("bob")
        with self.assertRaises(LoginLockedError):
            LoginThrottle.check("bob")

    def test_not_locked_below_threshold(self):
        for _ in range(LoginThrottle.MAX_FAILED_ATTEMPTS - 1):
            LoginThrottle.record_failure("carol")
        LoginThrottle.check("carol")  # should not raise

    def test_success_clears_failures(self):
        for _ in range(LoginThrottle.MAX_FAILED_ATTEMPTS - 1):
            LoginThrottle.record_failure("dave")
        LoginThrottle.record_success("dave")
        LoginThrottle.check("dave")  # should not raise
        self.assertNotIn("dave", LoginThrottle._attempts)

    def test_different_usernames_independent(self):
        for _ in range(LoginThrottle.MAX_FAILED_ATTEMPTS):
            LoginThrottle.record_failure("eve")
        LoginThrottle.check("frank")  # should not raise, different user


if __name__ == "__main__":
    unittest.main()
