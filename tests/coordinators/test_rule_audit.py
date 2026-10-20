# Copyright 2026 UniFi Insights contributors
"""Tests for rule change detection in the UniFi Insights config coordinator."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import pytest

from custom_components.unifi_insights.coordinators import (
    rule_audit as rule_audit_module,
)
from custom_components.unifi_insights.coordinators.rule_audit import (
    RULE_CHANGE_BUFFER_SIZE,
    RULE_FAMILIES,
    RULE_TYPES,
    RuleAuditTracker,
    RuleFamily,
)

_ALL_OK = {(family.data_key, "s1"): "ok" for family in RULE_FAMILIES}
_SIMULATED_ERROR = "simulated comparison error"


def _data(**sections: Any) -> dict[str, Any]:
    """Build coordinator data for site s1 with empty sections by default."""
    base: dict[str, Any] = {
        "sites": {"s1": {"id": "s1"}},
    }
    for family in RULE_FAMILIES:
        base[family.data_key] = {"s1": {}}
    base.update(sections)
    return base


def test_rule_types_cover_the_six_families() -> None:
    """The six audited rule families are registered in order."""
    assert RULE_TYPES == (
        "firewall_rule",
        "policy_based_route",
        "port_forward",
        "traffic_rule",
        "nat_rule",
        "static_route",
    )


def test_first_snapshot_is_a_silent_baseline() -> None:
    """The first refresh with successful fetch establishes baseline silently."""
    tracker = RuleAuditTracker()
    data = _data(
        firewall_rules={"s1": {"r1": {"id": "r1", "name": "R1", "enabled": True}}}
    )
    assert tracker.process(data, _ALL_OK) == {}


def test_unchanged_refresh_reports_nothing() -> None:
    """Subsequent refreshes with unchanged rules produce no change records."""
    tracker = RuleAuditTracker()
    data = _data(
        firewall_rules={"s1": {"r1": {"id": "r1", "name": "R1", "enabled": True}}}
    )
    tracker.process(data, _ALL_OK)
    assert tracker.process(data, _ALL_OK) == {}


@pytest.mark.parametrize("family", RULE_FAMILIES, ids=lambda f: f.rule_type)
def test_added_modified_deleted_for_each_family(family: RuleFamily) -> None:
    """Add, modify, and delete events are detected and sequenced for each family."""
    tracker = RuleAuditTracker()
    name_field = family.name_fields[0]
    rule_keep = {name_field: "Keep", "enabled": True, "action": "allow"}
    data1 = _data(**{family.data_key: {"s1": {"keep": rule_keep}}})
    tracker.process(data1, _ALL_OK)

    rule_keep_modified = {name_field: "Keep", "enabled": False, "action": "allow"}
    rule_new = {name_field: "New", "enabled": True, "action": "drop"}
    data2 = _data(
        **{family.data_key: {"s1": {"keep": rule_keep_modified, "new": rule_new}}}
    )
    changes = tracker.process(data2, _ALL_OK)

    assert [
        (c["change_type"], c["rule_type"], c["rule_id"]) for c in changes["s1"]
    ] == [
        ("added", family.rule_type, "new"),
        ("modified", family.rule_type, "keep"),
    ]
    mod_change = changes["s1"][1]
    assert mod_change["changed_fields"] == ["enabled"]
    assert mod_change["enabled"] is False
    assert mod_change["rule_name"] == "Keep"
    assert [c["sequence"] for c in changes["s1"]] == [1, 2]

    data3 = _data(**{family.data_key: {"s1": {}}})
    changes3_pending = tracker.process(data3, _ALL_OK)
    assert len(changes3_pending["s1"]) == 2

    changes3 = tracker.process(data3, _ALL_OK)
    deleted = changes3["s1"][-2:]
    assert [c["change_type"] for c in deleted] == ["deleted", "deleted"]
    by_id = {c["rule_id"]: c for c in deleted}
    assert by_id["keep"]["rule_name"] == "Keep"
    assert by_id["keep"]["enabled"] is False
    assert by_id["new"]["rule_name"] == "New"
    assert by_id["new"]["enabled"] is True
    assert changes3["s1"][-1]["sequence"] == 4


@pytest.mark.parametrize("outcome", ["unsupported", "failed", None])
def test_non_authoritative_fetch_keeps_baseline_and_reports_nothing(
    outcome: str | None,
) -> None:
    """Non-authoritative fetches keep the existing baseline and report nothing."""
    tracker = RuleAuditTracker()
    rule = {"name": "R1", "description": "R1", "enabled": True}
    data1 = _data(firewall_rules={"s1": {"r1": rule}})
    tracker.process(data1, _ALL_OK)

    if outcome is None:
        outcomes = {k: v for k, v in _ALL_OK.items() if k != ("firewall_rules", "s1")}
    else:
        outcomes = {**_ALL_OK, ("firewall_rules", "s1"): outcome}

    data_empty = _data(firewall_rules={"s1": {}})
    assert tracker.process(data_empty, outcomes) == {}

    # The same rules returning with ok report nothing because baseline was preserved
    assert tracker.process(data1, _ALL_OK) == {}


def test_section_first_seen_after_unsupported_is_a_silent_baseline() -> None:
    """A section first seen after unsupported establishes a silent baseline."""
    tracker = RuleAuditTracker()
    outcomes_unsupported = {**_ALL_OK, ("firewall_rules", "s1"): "unsupported"}
    tracker.process(_data(), outcomes_unsupported)

    data = _data(firewall_rules={"s1": {"r1": {"name": "R1", "enabled": True}}})
    assert tracker.process(data, _ALL_OK) == {}


def test_order_only_change_reports_nothing() -> None:
    """Volatile order keys (index, rule_index, metadata) do not trigger changes."""
    tracker = RuleAuditTracker()
    data1 = _data(
        firewall_rules={
            "s1": {
                "fw1": {
                    "name": "FW1",
                    "enabled": True,
                    "index": 1,
                    "metadata": {"origin": "USER"},
                }
            }
        },
        nat_rules={
            "s1": {
                "nat1": {
                    "description": "NAT1",
                    "enabled": True,
                    "rule_index": 1,
                    "setting_preference": "manual",
                }
            }
        },
    )
    tracker.process(data1, _ALL_OK)

    data2 = _data(
        firewall_rules={
            "s1": {
                "fw1": {
                    "name": "FW1",
                    "enabled": True,
                    "index": 2,
                    "metadata": {"origin": "MODIFIED"},
                }
            }
        },
        nat_rules={
            "s1": {
                "nat1": {
                    "description": "NAT1",
                    "enabled": True,
                    "rule_index": 2,
                    "setting_preference": "manual",
                }
            }
        },
    )
    assert tracker.process(data2, _ALL_OK) == {}


def test_predefined_rules_are_not_audited() -> None:
    """System-defined firewall and auto/predefined NAT rules are excluded from audit."""
    tracker = RuleAuditTracker()
    tracker.process(_data(), _ALL_OK)

    data = _data(
        firewall_rules={
            "s1": {
                "sys_fw": {
                    "name": "Sys",
                    "enabled": True,
                    "metadata": {"origin": "SYSTEM_DEFINED"},
                },
                "user_fw": {"name": "User FW", "enabled": True},
            }
        },
        nat_rules={
            "s1": {
                "pre_nat": {
                    "description": "Pre NAT",
                    "enabled": True,
                    "is_predefined": True,
                },
                "auto_nat": {
                    "description": "Auto NAT",
                    "enabled": True,
                    "setting_preference": "auto",
                },
                "manual_nat": {
                    "description": "Manual NAT",
                    "enabled": True,
                    "setting_preference": "manual",
                },
            }
        },
    )
    changes = tracker.process(data, _ALL_OK)
    added_ids = {c["rule_id"] for c in changes["s1"]}
    assert added_ids == {"user_fw", "manual_nat"}
    assert all(c["change_type"] == "added" for c in changes["s1"])


def test_baseline_is_not_aliased_to_coordinator_data() -> None:
    """Mutating coordinator data in place does not alter tracker baseline snapshots."""
    tracker = RuleAuditTracker()
    record = {"name": "R1", "enabled": True, "nested": ["a"]}
    data1 = _data(firewall_rules={"s1": {"r1": record}})
    tracker.process(data1, _ALL_OK)

    record["enabled"] = False
    record["nested"].append("b")
    changes = tracker.process(data1, _ALL_OK)
    assert changes["s1"][0]["changed_fields"] == ["enabled", "nested"]


def test_change_records_hold_names_never_values() -> None:
    """Change records contain metadata and field names, never field values."""
    tracker = RuleAuditTracker()
    record1 = {
        "name": "R1",
        "enabled": True,
        "src": "192.0.2.53",
        "dst": "198.51.100.0/24",
        "domain": "blocked.example",
        "gw": "10.9.9.9",
    }
    tracker.process(_data(firewall_rules={"s1": {"r1": record1}}), _ALL_OK)

    record2 = {
        "name": "R1",
        "enabled": False,
        "src": "192.0.2.54",
        "dst": "198.51.100.0/25",
        "domain": "other.example",
        "gw": "10.9.9.8",
    }
    changes = tracker.process(_data(firewall_rules={"s1": {"r1": record2}}), _ALL_OK)
    c = changes["s1"][0]
    expected_keys = {
        "change_type",
        "site_id",
        "rule_type",
        "rule_id",
        "rule_name",
        "enabled",
        "changed_fields",
        "sequence",
    }
    assert set(c.keys()) == expected_keys
    assert c["changed_fields"] == sorted(["domain", "dst", "enabled", "gw", "src"])
    dumped = json.dumps(changes)
    for forbidden in (
        "192.0.2.53",
        "198.51.100.0/24",
        "blocked.example",
        "10.9.9.9",
        "192.0.2.54",
        "198.51.100.0/25",
        "other.example",
        "10.9.9.8",
    ):
        assert forbidden not in dumped


def test_removed_site_drops_baseline_and_changes() -> None:
    """When a site is removed, its baseline and changes are purged; return is silent."""
    tracker = RuleAuditTracker()
    data = _data(firewall_rules={"s1": {"r1": {"name": "R1", "enabled": True}}})
    tracker.process(data, _ALL_OK)

    data2 = _data(firewall_rules={"s1": {"r1": {"name": "R1", "enabled": False}}})
    tracker.process(data2, _ALL_OK)
    assert "s1" in tracker.process(data2, _ALL_OK)

    res_removed = tracker.process({"sites": {}}, {})
    assert res_removed == {}

    res_returned = tracker.process(data, _ALL_OK)
    assert res_returned == {}


def test_sites_are_tracked_independently() -> None:
    """Different sites are diffed independently."""
    tracker = RuleAuditTracker()
    data1 = {
        "sites": {"s1": {"id": "s1"}, "s2": {"id": "s2"}},
        "firewall_rules": {
            "s1": {"r1": {"name": "R1", "enabled": True}},
            "s2": {"r2": {"name": "R2", "enabled": True}},
        },
    }
    outcomes1 = {
        ("firewall_rules", "s1"): "ok",
        ("firewall_rules", "s2"): "ok",
    }
    tracker.process(data1, outcomes1)

    data2 = {
        "sites": {"s1": {"id": "s1"}, "s2": {"id": "s2"}},
        "firewall_rules": {
            "s1": {},
            "s2": {},
        },
    }
    outcomes2 = {
        ("firewall_rules", "s1"): "failed",
        ("firewall_rules", "s2"): "ok",
    }
    res_pending = tracker.process(data2, outcomes2)
    assert res_pending == {}
    res = tracker.process(data2, outcomes2)
    assert list(res.keys()) == ["s2"]
    assert res["s2"][0]["site_id"] == "s2"
    assert res["s2"][0]["change_type"] == "deleted"


def test_buffer_is_bounded_and_overflow_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When changes exceed buffer size, oldest are dropped and a warning is logged."""
    tracker = RuleAuditTracker()
    tracker.process(_data(), _ALL_OK)

    added_rules = {
        f"r_{i}": {"name": f"Rule {i}", "enabled": True}
        for i in range(RULE_CHANGE_BUFFER_SIZE + 5)
    }
    with caplog.at_level(logging.WARNING):
        res = tracker.process(_data(firewall_rules={"s1": added_rules}), _ALL_OK)

    assert len(res["s1"]) == RULE_CHANGE_BUFFER_SIZE
    assert res["s1"][-1]["sequence"] == RULE_CHANGE_BUFFER_SIZE + 5
    assert any("only the newest" in record.message for record in caplog.records)
    assert not any("Rule 0" in record.message for record in caplog.records)

    # 60 changes, then 60 more: 100 are kept and the second batch logs no
    # overflow warning.
    tracker2 = RuleAuditTracker()
    tracker2.process(_data(), _ALL_OK)
    batch1 = {f"b1_{i}": {"name": f"B1 {i}", "enabled": True} for i in range(60)}
    tracker2.process(_data(firewall_rules={"s1": batch1}), _ALL_OK)

    caplog.clear()
    batch2 = dict(batch1)
    batch2.update({f"b2_{i}": {"name": f"B2 {i}", "enabled": True} for i in range(60)})
    with caplog.at_level(logging.WARNING):
        res2 = tracker2.process(_data(firewall_rules={"s1": batch2}), _ALL_OK)
    assert len(res2["s1"]) == RULE_CHANGE_BUFFER_SIZE
    assert not any("only the newest" in record.message for record in caplog.records)


@pytest.mark.parametrize(
    "malformed",
    [
        {},
        {"sites": None},
        {"sites": {"s1": {"id": "s1"}}, "firewall_rules": None},
        {"sites": {"s1": {"id": "s1"}}, "firewall_rules": {"s1": None}},
        {"sites": {"s1": {"id": "s1"}}, "firewall_rules": {"s1": {"r1": "x"}}},
        {"sites": {"s1": {"id": "s1"}}, "firewall_rules": {"s1": {7: {"name": "R"}}}},
    ],
)
def test_malformed_data_is_tolerated(malformed: dict[str, Any]) -> None:
    """Malformed coordinator data yields empty results without raising exceptions."""
    tracker = RuleAuditTracker()
    assert tracker.process(malformed, _ALL_OK) == {}
    assert tracker.process(malformed, _ALL_OK) == {}

    # One-rule baseline first, then malformed data -> {}, then the rule again -> {}
    good_data = _data(firewall_rules={"s1": {"r1": {"name": "R1", "enabled": True}}})
    tracker2 = RuleAuditTracker()
    tracker2.process(good_data, _ALL_OK)
    res_malformed = tracker2.process(malformed, _ALL_OK)
    assert res_malformed == {}
    res_again = tracker2.process(good_data, _ALL_OK)
    assert res_again == {}

    if malformed.get("firewall_rules") is None and malformed.get("sites"):
        # A malformed section does not count as empty, so a following empty
        # poll is still only held pending.
        empty_data = _data(firewall_rules={"s1": {}})
        tracker3 = RuleAuditTracker()
        tracker3.process(good_data, _ALL_OK)
        tracker3.process(malformed, _ALL_OK)
        assert tracker3.process(empty_data, _ALL_OK) == {}


def test_returned_lists_are_copies() -> None:
    """Mutating returned change lists does not mutate tracker state."""
    tracker = RuleAuditTracker()
    tracker.process(_data(), _ALL_OK)
    res = tracker.process(
        _data(firewall_rules={"s1": {"r1": {"name": "R1", "enabled": True}}}),
        _ALL_OK,
    )
    res["s1"].clear()
    res2 = tracker.process(
        _data(firewall_rules={"s1": {"r1": {"name": "R1", "enabled": True}}}),
        _ALL_OK,
    )
    assert len(res2["s1"]) == 1


def test_rule_name_uses_first_non_blank_field() -> None:
    """Rule name extraction uses the first non-blank field or falls back to None."""
    tracker = RuleAuditTracker()
    tracker.process(_data(), _ALL_OK)
    data = _data(
        policy_based_routes={
            "s1": {
                "p1": {"description": "P1 Desc", "name": "P1 Name", "enabled": True},
                "p2": {"description": "   ", "name": "P2 Name", "enabled": True},
                "p3": {"enabled": True},
            }
        }
    )
    res = tracker.process(data, _ALL_OK)
    names_by_id = {c["rule_id"]: c["rule_name"] for c in res["s1"]}
    assert names_by_id["p1"] == "P1 Desc"
    assert names_by_id["p2"] == "P2 Name"
    assert names_by_id["p3"] is None


def test_single_empty_poll_is_not_believed() -> None:
    """An empty section on one poll is held pending; rules returning reports nothing."""
    tracker = RuleAuditTracker()
    rules = {
        "pf1": {"name": "PF 1", "enabled": True},
        "pf2": {"name": "PF 2", "enabled": True},
    }
    tracker.process(_data(port_forwards={"s1": rules}), _ALL_OK)

    res_empty = tracker.process(_data(port_forwards={"s1": {}}), _ALL_OK)
    assert res_empty == {}

    res_back = tracker.process(_data(port_forwards={"s1": rules}), _ALL_OK)
    assert res_back == {}


def test_empty_confirmed_by_second_poll_reports_deleted() -> None:
    """A second empty poll confirms the first and reports every rule deleted."""
    tracker = RuleAuditTracker()
    rules = {
        "pf1": {"name": "PF 1", "enabled": True},
        "pf2": {"name": "PF 2", "enabled": False},
    }
    tracker.process(_data(port_forwards={"s1": rules}), _ALL_OK)

    res1 = tracker.process(_data(port_forwards={"s1": {}}), _ALL_OK)
    assert res1 == {}

    res2 = tracker.process(_data(port_forwards={"s1": {}}), _ALL_OK)
    changes = res2["s1"]
    assert len(changes) == 2
    assert [c["change_type"] for c in changes] == ["deleted", "deleted"]
    by_id = {c["rule_id"]: c for c in changes}
    assert by_id["pf1"]["rule_name"] == "PF 1"
    assert by_id["pf1"]["enabled"] is True
    assert by_id["pf2"]["rule_name"] == "PF 2"
    assert by_id["pf2"]["enabled"] is False


def test_failed_poll_between_two_empty_polls_still_confirms() -> None:
    """A failed poll between two empty polls neither confirms nor clears the wait."""
    tracker = RuleAuditTracker()
    rules = {
        "pf1": {"name": "PF 1", "enabled": True},
        "pf2": {"name": "PF 2", "enabled": True},
    }
    tracker.process(_data(port_forwards={"s1": rules}), _ALL_OK)

    res1 = tracker.process(_data(port_forwards={"s1": {}}), _ALL_OK)
    assert res1 == {}

    outcomes_failed = dict(_ALL_OK)
    outcomes_failed[("port_forwards", "s1")] = "failed"
    res_failed = tracker.process(_data(port_forwards={"s1": {}}), outcomes_failed)
    assert res_failed == {}

    res2 = tracker.process(_data(port_forwards={"s1": {}}), _ALL_OK)
    changes = res2["s1"]
    assert len(changes) == 2
    assert [c["change_type"] for c in changes] == ["deleted", "deleted"]


def test_rules_back_after_one_empty_poll_report_real_changes_only() -> None:
    """Rules returning after one empty poll diff against the retained baseline."""
    tracker = RuleAuditTracker()
    rules = {
        "pf1": {"name": "PF 1", "enabled": True},
        "pf2": {"name": "PF 2", "enabled": True},
    }
    tracker.process(_data(port_forwards={"s1": rules}), _ALL_OK)

    res_empty = tracker.process(_data(port_forwards={"s1": {}}), _ALL_OK)
    assert res_empty == {}

    rules_modified = {
        "pf1": {"name": "PF 1", "enabled": False},
        "pf2": {"name": "PF 2", "enabled": True},
    }
    res_back = tracker.process(_data(port_forwards={"s1": rules_modified}), _ALL_OK)
    changes = res_back["s1"]
    assert len(changes) == 1
    assert changes[0]["change_type"] == "modified"
    assert changes[0]["rule_id"] == "pf1"
    assert changes[0]["changed_fields"] == ["enabled"]

    # When emptied again after being settled, it must go pending again (kills P3)
    res_empty2 = tracker.process(_data(port_forwards={"s1": {}}), _ALL_OK)
    assert len(res_empty2["s1"]) == 1

    res_empty3 = tracker.process(_data(port_forwards={"s1": {}}), _ALL_OK)
    assert len(res_empty3["s1"]) == 3


def test_pending_empty_is_dropped_with_its_site() -> None:
    """When a site is removed, pending empty flags are dropped."""
    tracker = RuleAuditTracker()
    rules = {"pf1": {"name": "PF 1", "enabled": True}}
    tracker.process(_data(port_forwards={"s1": rules}), _ALL_OK)

    res1 = tracker.process(_data(port_forwards={"s1": {}}), _ALL_OK)
    assert res1 == {}

    res_removed = tracker.process({"sites": {}}, {})
    assert res_removed == {}

    res_returned = tracker.process(_data(port_forwards={"s1": rules}), _ALL_OK)
    assert res_returned == {}

    res2 = tracker.process(_data(port_forwards={"s1": {}}), _ALL_OK)
    assert res2 == {}


def test_error_in_one_family_keeps_other_changes_for_the_next_poll(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An error in one family keeps the whole site's changes for the next poll."""
    tracker = RuleAuditTracker()
    data1 = _data(
        firewall_rules={"s1": {"fw1": {"name": "FW 1", "enabled": True}}},
        static_routes={"s1": {"sr1": {"name": "SR 1", "enabled": True}}},
    )
    tracker.process(data1, _ALL_OK)

    data2 = _data(
        firewall_rules={"s1": {"fw1": {"name": "FW 1", "enabled": False}}},
        static_routes={"s1": {"sr1": {"name": "SR 1", "enabled": True}}},
    )
    real_snapshot = rule_audit_module._snapshot

    def failing_snapshot(family: RuleFamily, record: dict[str, Any]) -> Any:
        if family.rule_type == "static_route":
            raise RuntimeError(_SIMULATED_ERROR)
        return real_snapshot(family, record)

    monkeypatch.setattr(rule_audit_module, "_snapshot", failing_snapshot)

    with caplog.at_level(logging.ERROR):
        res1 = tracker.process(data2, _ALL_OK)
    assert res1 == {}
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) == 1

    monkeypatch.undo()

    res2 = tracker.process(data2, _ALL_OK)
    assert "s1" in res2
    assert len(res2["s1"]) == 1
    assert res2["s1"][0]["change_type"] == "modified"
    assert res2["s1"][0]["rule_id"] == "fw1"


def test_error_in_one_site_does_not_block_other_sites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error comparing one site does not stop comparisons of other sites."""
    tracker = RuleAuditTracker()
    data1 = {
        "sites": {"s1": {"id": "s1"}, "s2": {"id": "s2"}},
        "firewall_rules": {
            "s1": {"r1": {"name": "R1", "enabled": True}},
            "s2": {"r2": {"name": "R2", "enabled": True}},
        },
    }
    outcomes = {
        ("firewall_rules", "s1"): "ok",
        ("firewall_rules", "s2"): "ok",
    }
    for family in RULE_FAMILIES:
        if family.data_key != "firewall_rules":
            data1[family.data_key] = {"s1": {}, "s2": {}}
            outcomes[(family.data_key, "s1")] = "ok"
            outcomes[(family.data_key, "s2")] = "ok"

    tracker.process(data1, outcomes)

    data2 = dict(data1)
    data2["firewall_rules"] = {
        "s1": {"r1": {"name": "R1", "enabled": False}},
        "s2": {"r2": {"name": "R2", "enabled": False}},
    }

    real_snapshot = rule_audit_module._snapshot

    def failing_snapshot(family: RuleFamily, record: dict[str, Any]) -> Any:
        if record.get("name") == "R1":
            raise RuntimeError(_SIMULATED_ERROR)
        return real_snapshot(family, record)

    monkeypatch.setattr(rule_audit_module, "_snapshot", failing_snapshot)

    res = tracker.process(data2, outcomes)
    assert "s1" not in res or res["s1"] == []
    assert "s2" in res
    assert len(res["s2"]) == 1
    assert res["s2"][0]["rule_id"] == "r2"


def test_persistent_error_is_logged_once(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A site that fails repeatedly logs its error only once until it recovers."""
    tracker = RuleAuditTracker()
    data = _data(firewall_rules={"s1": {"r1": {"name": "R1", "enabled": True}}})
    tracker.process(data, _ALL_OK)

    def failing_snapshot(family: RuleFamily, record: dict[str, Any]) -> Any:
        raise RuntimeError(_SIMULATED_ERROR)

    monkeypatch.setattr(rule_audit_module, "_snapshot", failing_snapshot)

    with caplog.at_level(logging.ERROR):
        tracker.process(data, _ALL_OK)
        tracker.process(data, _ALL_OK)
        tracker.process(data, _ALL_OK)

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) == 1

    monkeypatch.undo()
    tracker.process(data, _ALL_OK)

    monkeypatch.setattr(rule_audit_module, "_snapshot", failing_snapshot)
    with caplog.at_level(logging.ERROR):
        tracker.process(data, _ALL_OK)

    error_records_after = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records_after) == 2


class _DummyEnum(Enum):
    ALPHA = "alpha"
    BETA = "beta"


def test_nested_key_order_is_not_a_change() -> None:
    """Nested key order differences do not trigger a change."""
    tracker = RuleAuditTracker()
    tracker.process(
        _data(
            firewall_rules={
                "s1": {"r1": {"name": "R", "enabled": True, "src": {"a": 1, "b": 2}}}
            }
        ),
        _ALL_OK,
    )
    res = tracker.process(
        _data(
            firewall_rules={
                "s1": {"r1": {"name": "R", "enabled": True, "src": {"b": 2, "a": 1}}}
            }
        ),
        _ALL_OK,
    )
    assert res == {}


def test_non_json_values_are_compared_not_raised() -> None:
    """Records with non-JSON types (datetime, Enum) compare cleanly via default=str."""
    tracker = RuleAuditTracker()
    r1 = {
        "name": "R",
        "enabled": True,
        "ts": datetime(2026, 1, 1, tzinfo=UTC),
        "status": _DummyEnum.ALPHA,
    }
    tracker.process(_data(firewall_rules={"s1": {"r1": r1}}), _ALL_OK)

    # Unchanged
    res_same = tracker.process(_data(firewall_rules={"s1": {"r1": r1}}), _ALL_OK)
    assert res_same == {}

    # Changed
    r2 = {
        "name": "R",
        "enabled": True,
        "ts": datetime(2026, 1, 2, tzinfo=UTC),
        "status": _DummyEnum.BETA,
    }
    res_diff = tracker.process(_data(firewall_rules={"s1": {"r1": r2}}), _ALL_OK)
    assert len(res_diff["s1"]) == 1
    assert res_diff["s1"][0]["change_type"] == "modified"


def test_removed_key_is_a_changed_field() -> None:
    """A key removed in the new record is reported in changed_fields."""
    tracker = RuleAuditTracker()
    tracker.process(
        _data(
            firewall_rules={"s1": {"r1": {"name": "R", "enabled": True, "note": "x"}}}
        ),
        _ALL_OK,
    )
    res = tracker.process(
        _data(firewall_rules={"s1": {"r1": {"name": "R", "enabled": True}}}),
        _ALL_OK,
    )
    assert len(res["s1"]) == 1
    assert res["s1"][0]["changed_fields"] == ["note"]


@pytest.mark.parametrize("val", ["true", 1])
def test_non_bool_enabled_is_reported_as_none(val: Any) -> None:
    """Non-boolean enabled values are reported as None in change records."""
    tracker = RuleAuditTracker()
    tracker.process(_data(firewall_rules={"s1": {}}), _ALL_OK)
    res = tracker.process(
        _data(firewall_rules={"s1": {"r1": {"name": "R", "enabled": val}}}),
        _ALL_OK,
    )
    assert len(res["s1"]) == 1
    assert res["s1"][0]["enabled"] is None
