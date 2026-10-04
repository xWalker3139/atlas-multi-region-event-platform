import unittest
from atlas.failover import decision


class FailoverTests(unittest.TestCase):
    def test_api_failure_does_not_promote_healthy_database(self):
        self.assertEqual(decision(False, True, True, 2, True), ('healthy_database', 0))

    def test_three_consecutive_observations(self):
        failures = 0
        for expected in ('suspect', 'suspect', 'promote'):
            action, failures = decision(False, False, True, failures, True)
            self.assertEqual(action, expected)

    def test_recovery_resets_suspicion(self):
        self.assertEqual(decision(True, False, True, 2, True)[1], 0)

    def test_broken_standby_blocks_promotion(self):
        self.assertEqual(decision(False, False, False, 9, True), ('standby_service_unavailable', 0))

    def test_no_automatic_failback(self):
        self.assertEqual(decision(False, False, True, 3, False), ('already_failed_over', 0))
