import os
import sys
from unittest import mock

import pytest
from botocore.exceptions import ClientError

os.environ["AWS_REGION"] = "eu-west-1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../lambda"))

import guardrail  # noqa: E402
from guardrail import (  # noqa: E402
    HOURS_PER_MONTH,
    estimate_monthly_cost,
    get_endpoint_instances,
    handle_resource,
    handler,
    is_idle,
)


def _client_error(op="Op", code="AccessDenied"):
    return ClientError({"Error": {"Code": code, "Message": ""}}, op)


def _resource(tags, kind="notebook", instance_type="ml.t3.xlarge", instances=None):
    r = {"name": "test-res", "kind": kind, "arn": "arn:test", "tags": tags}
    if kind == "notebook":
        r["instance_type"] = instance_type
    else:
        r["instances"] = instances if instances is not None else [("ml.m5.xlarge", 1)]
    return r


@pytest.fixture
def live(monkeypatch):
    """Disable dry-run so stop calls actually reach the (mocked) client."""
    monkeypatch.setattr(guardrail, "DRY_RUN", False)


@pytest.fixture
def sns_topic(monkeypatch):
    monkeypatch.setattr(guardrail, "SNS_TOPIC_ARN", "arn:aws:sns:eu-west-1:123:t")


# ── cost estimate ─────────────────────────────────────────────────────────────


class TestEstimateMonthlyCost:
    def test_known_notebook_type(self):
        cost, reliable = estimate_monthly_cost({"instance_type": "ml.t3.medium"})
        assert cost == round(0.05 * HOURS_PER_MONTH, 2) and reliable

    def test_unknown_type_uses_fallback_and_is_flagged(self):
        cost, reliable = estimate_monthly_cost({"instance_type": "ml.future.9xl"})
        assert cost == round(0.10 * HOURS_PER_MONTH, 2) and not reliable

    def test_endpoint_sums_variants_times_count(self):
        cost, _ = estimate_monthly_cost(
            {"instances": [("ml.m5.xlarge", 2), ("ml.t3.medium", 1)]}
        )
        assert cost == round((0.25 * 2 + 0.05) * HOURS_PER_MONTH, 2)

    def test_serverless_variant_costs_nothing(self):
        cost, reliable = estimate_monthly_cost({"instances": [("serverless", 0)]})
        assert cost == 0.0 and reliable

    def test_endpoint_without_config_falls_back(self):
        # describe failed → instances == [] → fallback single unknown instance
        cost, reliable = estimate_monthly_cost({"instances": []})
        assert cost == round(0.10 * HOURS_PER_MONTH, 2) and not reliable


class TestGetEndpointInstances:
    def test_reads_variants_from_config(self):
        sm = mock.MagicMock()
        sm.describe_endpoint.return_value = {"EndpointConfigName": "cfg"}
        sm.describe_endpoint_config.return_value = {
            "ProductionVariants": [
                {"InstanceType": "ml.m5.xlarge", "InitialInstanceCount": 2},
                {"ServerlessConfig": {"MemorySizeInMB": 2048}},
            ]
        }
        with mock.patch("guardrail.sm", sm):
            assert get_endpoint_instances("ep") == [
                ("ml.m5.xlarge", 2),
                ("serverless", 0),
            ]

    def test_describe_error_returns_empty(self):
        sm = mock.MagicMock()
        sm.describe_endpoint.side_effect = _client_error("DescribeEndpoint")
        with mock.patch("guardrail.sm", sm):
            assert get_endpoint_instances("ep") == []


# ── idle detection ────────────────────────────────────────────────────────────


class TestIsIdle:
    def _cw(self, datapoints):
        cw = mock.MagicMock()
        cw.get_metric_statistics.return_value = {"Datapoints": datapoints}
        return cw

    def test_notebook_idle_when_low_cpu(self):
        with mock.patch("guardrail.cw", self._cw([{"Average": 1.0}])):
            assert is_idle({"name": "nb", "kind": "notebook"}) is True

    def test_notebook_active_when_high_cpu(self):
        with mock.patch("guardrail.cw", self._cw([{"Average": 80.0}])):
            assert is_idle({"name": "nb", "kind": "notebook"}) is False

    def test_notebook_idle_when_no_datapoints(self):
        with mock.patch("guardrail.cw", self._cw([])):
            assert is_idle({"name": "nb", "kind": "notebook"}) is True

    def test_notebook_queries_notebook_namespace(self):
        cw = self._cw([])
        with mock.patch("guardrail.cw", cw):
            is_idle({"name": "nb", "kind": "notebook"})
        assert (
            cw.get_metric_statistics.call_args.kwargs["Namespace"]
            == "/aws/sagemaker/NotebookInstances"
        )

    def test_endpoint_idle_when_zero_invocations(self):
        with mock.patch("guardrail.cw", self._cw([])):
            assert is_idle({"name": "ep", "kind": "endpoint"}) is True

    def test_endpoint_active_when_invocations_present(self):
        with mock.patch("guardrail.cw", self._cw([{"Sum": 100.0}])):
            assert is_idle({"name": "ep", "kind": "endpoint"}) is False

    def test_endpoint_query_includes_variant_dimension(self):
        cw = self._cw([])
        with mock.patch("guardrail.cw", cw):
            is_idle({"name": "ep", "kind": "endpoint"})
        dims = cw.get_metric_statistics.call_args.kwargs["Dimensions"]
        assert {"Name": "VariantName", "Value": "AllTraffic"} in dims

    def test_client_error_means_not_idle(self):
        cw = mock.MagicMock()
        cw.get_metric_statistics.side_effect = _client_error("GetMetricStatistics")
        with mock.patch("guardrail.cw", cw):
            assert is_idle({"name": "nb", "kind": "notebook"}) is False

    def test_unknown_kind_not_idle(self):
        with mock.patch("guardrail.cw", self._cw([])):
            assert is_idle({"name": "x", "kind": "training-job"}) is False


# ── policy ────────────────────────────────────────────────────────────────────


class TestPolicy:
    def test_critical_notifies_and_never_stops(self, live, sns_topic):
        sm, sns = mock.MagicMock(), mock.MagicMock()
        with (
            mock.patch("guardrail.sm", sm),
            mock.patch("guardrail.sns", sns),
            mock.patch("guardrail.is_idle", return_value=True),
        ):
            assert (
                handle_resource(
                    _resource({"DataCriticality": "high", "AutoStop": "true"})
                )
                == "notify_critical"
            )
        sm.stop_notebook_instance.assert_not_called()
        sns.publish.assert_called_once()

    def test_prod_above_threshold_escalates(self, live, sns_topic):
        sns = mock.MagicMock()
        with mock.patch("guardrail.sns", sns):
            assert (
                handle_resource(
                    _resource({"Environment": "prod"}, instance_type="ml.p3.2xlarge")
                )
                == "escalate_prod"
            )
        assert "above threshold" in sns.publish.call_args.kwargs["Subject"]

    def test_prod_below_threshold_is_silent(self, live, sns_topic):
        sns = mock.MagicMock()
        with mock.patch("guardrail.sns", sns):
            assert (
                handle_resource(
                    _resource({"Environment": "prod"}, instance_type="ml.t3.medium")
                )
                == "prod_ok"
            )
        sns.publish.assert_not_called()

    def test_prod_never_auto_stops_even_with_autostop_tag(self, live, sns_topic):
        sm = mock.MagicMock()
        with (
            mock.patch("guardrail.sm", sm),
            mock.patch("guardrail.is_idle", return_value=True),
        ):
            handle_resource(
                _resource(
                    {"Environment": "prod", "AutoStop": "true"},
                    instance_type="ml.t3.medium",
                )
            )
        sm.stop_notebook_instance.assert_not_called()

    def test_autostop_idle_notebook_is_stopped(self, live, sns_topic):
        sm, sns = mock.MagicMock(), mock.MagicMock()
        with (
            mock.patch("guardrail.sm", sm),
            mock.patch("guardrail.sns", sns),
            mock.patch("guardrail.is_idle", return_value=True),
        ):
            assert handle_resource(_resource({"AutoStop": "true"})) == "stopped"
        sm.stop_notebook_instance.assert_called_once_with(
            NotebookInstanceName="test-res"
        )
        assert "stopped" in sns.publish.call_args.kwargs["Subject"]

    def test_dry_run_does_not_stop_but_reports(self, sns_topic):
        # DRY_RUN is the module default (env unset → true)
        sm, sns = mock.MagicMock(), mock.MagicMock()
        with (
            mock.patch("guardrail.sm", sm),
            mock.patch("guardrail.sns", sns),
            mock.patch("guardrail.is_idle", return_value=True),
        ):
            assert handle_resource(_resource({"AutoStop": "true"})) == "stop_skipped"
        sm.stop_notebook_instance.assert_not_called()
        assert sns.publish.call_args.kwargs["Subject"].startswith("[DRY_RUN]")

    def test_autostop_active_notebook_is_left_alone(self, live, sns_topic):
        sm, sns = mock.MagicMock(), mock.MagicMock()
        with (
            mock.patch("guardrail.sm", sm),
            mock.patch("guardrail.sns", sns),
            mock.patch("guardrail.is_idle", return_value=False),
        ):
            assert handle_resource(_resource({"AutoStop": "true"})) == "monitor"
        sm.stop_notebook_instance.assert_not_called()
        sns.publish.assert_not_called()

    def test_autostop_cheap_notebook_is_not_checked(self, live, sns_topic):
        # cost below IDLE_COST_THRESHOLD → is_idle never even called (saves CloudWatch calls)
        with (
            mock.patch("guardrail.is_idle") as idle,
            mock.patch("guardrail.IDLE_COST_THRESHOLD", 1000),
        ):
            assert handle_resource(_resource({"AutoStop": "true"})) == "monitor"
        idle.assert_not_called()

    def test_idle_endpoint_is_notified_not_deleted(self, live, sns_topic):
        sm, sns = mock.MagicMock(), mock.MagicMock()
        with (
            mock.patch("guardrail.sm", sm),
            mock.patch("guardrail.sns", sns),
            mock.patch("guardrail.is_idle", return_value=True),
        ):
            assert (
                handle_resource(_resource({"AutoStop": "true"}, kind="endpoint"))
                == "notify_idle_endpoint"
            )
        sm.delete_endpoint.assert_not_called()
        sm.stop_notebook_instance.assert_not_called()
        assert "Idle endpoint" in sns.publish.call_args.kwargs["Subject"]

    def test_default_is_log_only(self, live, sns_topic):
        sns = mock.MagicMock()
        with mock.patch("guardrail.sns", sns):
            assert handle_resource(_resource({})) == "monitor"
        sns.publish.assert_not_called()

    def test_unknown_instance_type_is_flagged_in_message(self, live, sns_topic):
        sns = mock.MagicMock()
        with mock.patch("guardrail.sns", sns):
            handle_resource(
                _resource({"DataCriticality": "high"}, instance_type="ml.new.type")
            )
        assert "fallback price" in sns.publish.call_args.kwargs["Message"]

    def test_notify_without_topic_is_noop(self, monkeypatch):
        monkeypatch.setattr(guardrail, "SNS_TOPIC_ARN", None)
        sns = mock.MagicMock()
        with mock.patch("guardrail.sns", sns):
            handle_resource(_resource({"DataCriticality": "high"}))
        sns.publish.assert_not_called()


# ── handler ───────────────────────────────────────────────────────────────────


class TestHandler:
    def _sm(self, notebooks=(), endpoints=()):
        sm = mock.MagicMock()

        def paginator(op):
            p = mock.MagicMock()
            key = (
                "NotebookInstances" if op == "list_notebook_instances" else "Endpoints"
            )
            p.paginate.return_value = [
                {key: list(notebooks if key == "NotebookInstances" else endpoints)}
            ]
            return p

        sm.get_paginator.side_effect = paginator
        sm.list_tags.return_value = {"Tags": []}
        sm.describe_endpoint.return_value = {"EndpointConfigName": "cfg"}
        sm.describe_endpoint_config.return_value = {
            "ProductionVariants": [
                {"InstanceType": "ml.m5.xlarge", "InitialInstanceCount": 1}
            ]
        }
        return sm

    def test_skips_resources_not_in_service(self):
        sm = self._sm(
            notebooks=[
                {
                    "NotebookInstanceName": "a",
                    "NotebookInstanceStatus": "Stopped",
                    "NotebookInstanceArn": "arn:a",
                },
                {
                    "NotebookInstanceName": "b",
                    "NotebookInstanceStatus": "InService",
                    "NotebookInstanceArn": "arn:b",
                    "InstanceType": "ml.t3.medium",
                },
            ],
            endpoints=[
                {
                    "EndpointName": "e",
                    "EndpointStatus": "Creating",
                    "EndpointArn": "arn:e",
                }
            ],
        )
        with (
            mock.patch("guardrail.sm", sm),
            mock.patch("guardrail.handle_resource", return_value="monitor") as hr,
        ):
            result = handler({}, None)
        assert hr.call_count == 1
        assert result == {
            "statusCode": 200,
            "dry_run": True,
            "decisions": {"monitor": 1},
        }

    def test_endpoint_instances_are_collected(self):
        sm = self._sm(
            endpoints=[
                {
                    "EndpointName": "e",
                    "EndpointStatus": "InService",
                    "EndpointArn": "arn:e",
                }
            ]
        )
        with (
            mock.patch("guardrail.sm", sm),
            mock.patch("guardrail.handle_resource", return_value="monitor") as hr,
        ):
            handler({}, None)
        assert hr.call_args.args[0]["instances"] == [("ml.m5.xlarge", 1)]

    def test_listing_error_does_not_crash(self):
        sm = mock.MagicMock()
        sm.get_paginator.side_effect = _client_error("ListNotebookInstances")
        with mock.patch("guardrail.sm", sm):
            assert handler({}, None)["statusCode"] == 200
