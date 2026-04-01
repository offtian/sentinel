from __future__ import annotations

import attrs.exceptions
import pytest

from sentinel.domain.pipeline import errors


class TestNodeError:
    def test_captures_node_name_and_message(self) -> None:
        # Given an error from the ClassifyAlert node
        error = errors.NodeError(
            node_name="ClassifyAlert",
            error_type="LLMTimeout",
            message="Request timed out after 30s",
            is_recoverable=True,
        )

        # When we inspect the error fields

        # Then all fields are captured correctly
        assert error.node_name == "ClassifyAlert"
        assert error.error_type == "LLMTimeout"
        assert error.message == "Request timed out after 30s"
        assert error.is_recoverable is True

    def test_defaults_to_non_recoverable(self) -> None:
        # Given an error with no explicit recoverability

        # When we create a NodeError without is_recoverable
        error = errors.NodeError(
            node_name="ClassifyAlert",
            error_type="Unknown",
            message="Something broke",
        )

        # Then it defaults to non-recoverable
        assert error.is_recoverable is False

    def test_is_frozen(self) -> None:
        # Given a node error
        error = errors.NodeError(
            node_name="ClassifyAlert",
            error_type="LLMTimeout",
            message="timeout",
        )

        # When we attempt to mutate it

        # Then it raises FrozenInstanceError
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            error.node_name = "Other"  # type: ignore[misc]


class TestPipelineNodeFailed:
    def test_wraps_node_error_as_exception(self) -> None:
        # Given a node error
        node_error = errors.NodeError(
            node_name="ClassifyAlert",
            error_type="LLMTimeout",
            message="Request timed out",
        )

        # When we create the exception
        exc = errors.PipelineNodeFailed(node_error=node_error)

        # Then it carries the node error and is a proper exception
        assert exc.node_error is node_error
        assert "ClassifyAlert" in str(exc)
        assert "Request timed out" in str(exc)

    def test_is_an_exception(self) -> None:
        # Given a PipelineNodeFailed instance
        node_error = errors.NodeError(
            node_name="X",
            error_type="Y",
            message="Z",
        )
        exc = errors.PipelineNodeFailed(node_error=node_error)

        # Then it is a proper Exception subclass
        assert isinstance(exc, Exception)
