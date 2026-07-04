"""Harness: turn an EvalCase into pipeline inputs and collect predictions."""

from evals.harness.prediction import Prediction
from evals.harness.ticket_builder import build_jira_ticket

__all__ = ["Prediction", "build_jira_ticket"]
