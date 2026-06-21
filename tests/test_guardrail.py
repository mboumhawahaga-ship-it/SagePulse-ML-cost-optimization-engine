import os
import sys
from unittest import mock

from botocore.exceptions import ClientError

os.environ["AWS_REGION"] = "eu-west-1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../lambda"))

from guardrail import (
    get_cost,
    handle_resource,
    handler,
    is_idle,
)

# ── get_cost ──────────────────────────────────────────────────────────────────


class TestGetCost:
    def test_known_instance_type(self):
        assert get_cost({"instance_type": "ml.t3.medium"}) == round(0.05 * 730, 2)

    def test_unknown_instance_type_uses_fallback(self):
        assert get_cost({"instance_type": "ml.unknown"}) == round(0.10 * 730, 2)

    def test_missing_instance_type_uses_fallback(self):
        assert get_cost({}) == round(0.10 * 730, 2)


# ── is_idle ───────────────────────────────────────────────────────────────────


class TestIsIdle:
    def _mock_cw(self, datapoints):
        cw = mock.MagicMock()
        cw.get_metric_statistics.return_value = {"Datapoints": datapoints}
        return cw

    def test_notebook_idle_when_low_cpu(self):
        with mock.patch("guardrail.cw", self._mock_cw([{"Average": 1.0}])):
            assert is_idle({"name": "nb", "kind": "notebook"}) is True

    def test_notebook_active_when_high_cpu(self):
        with mock.patch("guardrail.cw", self._mock_cw([{"Average": 80.0}])):
            assert is_idle({"name": "nb", "kind": "notebook"}) is False

    def test_notebook_idle_when_no_datapoints(self):
        with mock.patch("guardrail.cw", self._mock_cw([])):
            assert is_idle({"name": "nb", "kind": "notebook"}) is True

    def test_endpoint_idle_when_zero_invocations(self):
        with mock.patch("guardrail.cw", self._mock_cw([])):
            assert is_idle({"name": "ep", "kind": "endpoint"}) is True

    def test_endpoint_active_when_invocations_present(self):
        with mock.patch("guardrail.cw", self._mock_cw([{"Sum": 100.0}])):
            assert is_idle({"name": "ep", "kind": "endpoint"}) is False

    def test_returns_false_on_client_error(self):
        cw = mock.MagicMock()
        cw.get_metric_statistics.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": ""}}, "GetMetricStatistics"
        )
        with mock.patch("guardrail.cw", cw):
            assert is_idle({"name": "nb", "kind": "notebook"}) is False


# ── handle_resource ───────────────────────────────────────────────────────────


class TestHandleResource:
    def _resource(self, tags, kind="notebook", instance_type="ml.t3.medium"):
        return {
            "name": "test-res",
            "kind": kind,
            "arn": "arn:test",
            "instance_type": instance_type,
            "tags": tags,
        }

    # Case 1 — HIGH CRITICALITY
    def test_case1_high_criticality_notify_only(self):
        with (
            mock.patch("guardrail.notify_only") as m_notify,
            mock.patch("guardrail.is_idle", return_value=True),
            mock.patch("guardrail.get_cost", return_value=200.0),
        ):
            handle_resource(self._resource({"DataCriticality": "high"}))
        m_notify.assert_called_once()

    def test_case1_does_not_stop(self):
        with (
            mock.patch("guardrail.stop_resource") as m_stop,
            mock.patch("guardrail.notify_only"),
            mock.patch("guardrail.is_idle", return_value=True),
            mock.patch("guardrail.get_cost", return_value=200.0),
        ):
            handle_resource(self._resource({"DataCriticality": "high"}))
        m_stop.assert_not_called()

    # Case 2 — PROD
    def test_case2_prod_no_stop(self):
        with (
            mock.patch("guardrail.stop_resource") as m_stop,
            mock.patch("guardrail.is_idle", return_value=True),
            mock.patch("guardrail.get_cost", return_value=200.0),
        ):
            handle_resource(self._resource({"Environment": "prod"}))
        m_stop.assert_not_called()

    def test_case2_prod_high_cost_escalates(self):
        with (
            mock.patch("guardrail.alert_escalation") as m_esc,
            mock.patch("guardrail.is_idle", return_value=True),
            mock.patch("guardrail.get_cost", return_value=200.0),
        ):
            handle_resource(self._resource({"Environment": "prod"}))
        m_esc.assert_called_once()

    def test_case2_prod_low_cost_no_escalation(self):
        with (
            mock.patch("guardrail.alert_escalation") as m_esc,
            mock.patch("guardrail.is_idle", return_value=True),
            mock.patch("guardrail.get_cost", return_value=5.0),
        ):
            handle_resource(self._resource({"Environment": "prod"}))
        m_esc.assert_not_called()

    # Case 3 — SAFE AUTO STOP
    def test_case3_dev_autostop_idle_above_threshold_stops(self):
        with (
            mock.patch("guardrail.stop_resource") as m_stop,
            mock.patch("guardrail.notify_slack"),
            mock.patch("guardrail.is_idle", return_value=True),
            mock.patch("guardrail.get_cost", return_value=50.0),
        ):
            handle_resource(self._resource({"AutoStop": "true", "Environment": "dev"}))
        m_stop.assert_called_once()

    def test_case3_no_stop_if_not_idle(self):
        with (
            mock.patch("guardrail.stop_resource") as m_stop,
            mock.patch("guardrail.notify_slack"),
            mock.patch("guardrail.is_idle", return_value=False),
            mock.patch("guardrail.get_cost", return_value=50.0),
        ):
            handle_resource(self._resource({"AutoStop": "true", "Environment": "dev"}))
        m_stop.assert_not_called()

    def test_case3_no_stop_if_cost_below_threshold(self):
        with (
            mock.patch("guardrail.stop_resource") as m_stop,
            mock.patch("guardrail.notify_slack"),
            mock.patch("guardrail.is_idle", return_value=True),
            mock.patch("guardrail.get_cost", return_value=1.0),
        ):
            handle_resource(self._resource({"AutoStop": "true", "Environment": "dev"}))
        m_stop.assert_not_called()

    # Case 4 — DEFAULT
    def test_case4_default_monitoring_only(self):
        with (
            mock.patch("guardrail.notify_slack") as m_slack,
            mock.patch("guardrail.is_idle", return_value=False),
            mock.patch("guardrail.get_cost", return_value=20.0),
        ):
            handle_resource(self._resource({}))
        m_slack.assert_called_once_with(mock.ANY, action="monitoring only")


# ── handler ───────────────────────────────────────────────────────────────────


class TestHandler:
    def _make_sm(self, notebooks=None, endpoints=None):
        sm = mock.MagicMock()

        def paginator_side_effect(name):
            p = mock.MagicMock()
            if name == "list_notebook_instances":
                p.paginate.return_value = [{"NotebookInstances": notebooks or []}]
            elif name == "list_endpoints":
                p.paginate.return_value = [{"Endpoints": endpoints or []}]
            return p

        sm.get_paginator.side_effect = paginator_side_effect
        sm.list_tags.return_value = {"Tags": []}
        return sm

    def test_returns_200(self):
        sm = self._make_sm()
        with mock.patch("guardrail.sm", sm):
            assert handler({}, None) == {"statusCode": 200}

    def test_calls_handle_resource_for_each_active_notebook(self):
        sm = self._make_sm(
            notebooks=[
                {
                    "NotebookInstanceName": "nb-1",
                    "NotebookInstanceArn": "arn:1",
                    "NotebookInstanceStatus": "InService",
                    "InstanceType": "ml.t3.medium",
                },
                {
                    "NotebookInstanceName": "nb-2",
                    "NotebookInstanceArn": "arn:2",
                    "NotebookInstanceStatus": "InService",
                    "InstanceType": "ml.t3.medium",
                },
            ]
        )
        with (
            mock.patch("guardrail.sm", sm),
            mock.patch("guardrail.handle_resource") as m_handle,
        ):
            handler({}, None)
        assert m_handle.call_count == 2

    def test_skips_stopped_resources(self):
        sm = self._make_sm(
            notebooks=[
                {
                    "NotebookInstanceName": "nb-stopped",
                    "NotebookInstanceArn": "arn:1",
                    "NotebookInstanceStatus": "Stopped",
                    "InstanceType": "ml.t3.medium",
                },
            ]
        )
        with (
            mock.patch("guardrail.sm", sm),
            mock.patch("guardrail.handle_resource") as m_handle,
        ):
            handler({}, None)
        m_handle.assert_not_called()

    def test_does_not_crash_on_sagemaker_error(self):
        sm = mock.MagicMock()
        sm.get_paginator.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": ""}}, "ListNotebookInstances"
        )
        with mock.patch("guardrail.sm", sm):
            result = handler({}, None)
        assert result["statusCode"] == 200
