"""Tests for compute_alert_signature in domain.memory.operations."""

from __future__ import annotations

from sentinel.domain.memory import operations as memory_operations


class TestComputeAlertSignature:
    def test_returns_16_char_hex_string(self) -> None:
        # Given a small set of labels and a category

        # When computing the signature
        signature = memory_operations.compute_alert_signature(
            labels=("severity:high", "service:api"),
            classification_category="oom_kill",
        )

        # Then a 16-char hex string is returned
        assert len(signature) == 16
        assert all(c in "0123456789abcdef" for c in signature)

    def test_is_order_stable_across_label_orderings(self) -> None:
        # Given two equivalent label sets in different orders
        labels_a = ("severity:high", "service:api", "region:eu")
        labels_b = ("region:eu", "service:api", "severity:high")

        # When computing signatures for both
        sig_a = memory_operations.compute_alert_signature(
            labels=labels_a,
            classification_category="oom_kill",
        )
        sig_b = memory_operations.compute_alert_signature(
            labels=labels_b,
            classification_category="oom_kill",
        )

        # Then both produce identical fingerprints (order-stable)
        assert sig_a == sig_b

    def test_different_categories_produce_different_signatures(self) -> None:
        # Given identical labels but different categories
        labels = ("severity:high", "service:api")

        # When computing signatures
        sig_oom = memory_operations.compute_alert_signature(
            labels=labels, classification_category="oom_kill"
        )
        sig_crash = memory_operations.compute_alert_signature(
            labels=labels, classification_category="crashloop"
        )

        # Then the two signatures differ (category is part of the fingerprint)
        assert sig_oom != sig_crash
