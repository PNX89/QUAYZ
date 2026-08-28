"""The chart's values, its schema and its templates, checked without helm.

WHY THIS IS SPLIT IN TWO. Helm's schema validation is Helm's, written in Go, and asserting it
here would be asserting a different implementation. So the BEHAVIOUR (does helm refuse a bad
value) is proved in the toolbox CI job, where helm exists, and what is proved here is the thing
that rots silently: values.yaml growing a key the schema does not constrain, or the schema
constraining one the chart no longer has. Every hole the schema was written for came from that
kind of gap rather than from a bad regular expression.

MEASURED, AND THE REASON THE SCHEMA EXISTS. Against the chart as it was:
  --set-string failure.crashLoop=false   installed a crash-looping pod
  --set failure.crashloop=true           installed a healthy one, with no warning
  --set image.pullPolicy=y               rendered a YAML boolean the API server rejects
  --set image.digest=notadigest          rendered busybox@notadigest
`helm lint` reported "0 chart(s) failed" for all four.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
CHART = REPO / "charts" / "deploy-canary"
TEMPLATES = CHART / "templates"


def values() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    return loaded


def schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
    return loaded


def rendered_templates() -> str:
    """Both suffixes. The image helper lives in _helpers.tpl, and globbing *.yaml alone made a
    value that IS read look unread, which is the exact failure these tests are here to catch."""
    files = sorted(TEMPLATES.glob("*.yaml")) + sorted(TEMPLATES.glob("*.tpl"))
    return "\n".join(path.read_text(encoding="utf-8") for path in files)


def paths(node: Any, prefix: str = "") -> set[str]:
    """Every dotted leaf path in a nested mapping."""
    if not isinstance(node, dict):
        return {prefix}
    found: set[str] = set()
    for key, value in node.items():
        found |= paths(value, f"{prefix}.{key}" if prefix else str(key))
    return found


def constrained(node: dict[str, Any], prefix: str = "") -> set[str]:
    """Every dotted leaf path the schema declares a type for."""
    properties = node.get("properties")
    if not isinstance(properties, dict):
        return {prefix} if prefix else set()
    found: set[str] = set()
    for key, value in properties.items():
        target = value
        ref = value.get("$ref") if isinstance(value, dict) else None
        if ref:
            target = SCHEMA
            for step in ref.removeprefix("#/").split("/"):
                target = target[step]
        found |= constrained(target, f"{prefix}.{key}" if prefix else str(key))
    return found


SCHEMA = schema()


def test_every_value_the_chart_ships_is_constrained_by_the_schema() -> None:
    """A value the schema does not mention is a value any string can be set to."""
    unconstrained = paths(values()) - constrained(SCHEMA)
    assert not unconstrained, f"values.yaml has keys the schema does not constrain: {unconstrained}"


def test_the_schema_constrains_nothing_the_chart_does_not_have() -> None:
    """The other direction. A rule for a deleted value reads as protection and is not."""
    orphaned = constrained(SCHEMA) - paths(values())
    assert not orphaned, f"the schema constrains keys values.yaml does not have: {orphaned}"


def test_the_three_switches_are_declared_boolean_and_nothing_else() -> None:
    """The whole reason the schema was added: "false" is a non-empty string and truthy."""
    switches = SCHEMA["properties"]["failure"]["properties"]
    assert set(switches) == {"neverReady", "crashLoop", "outOfMemory", "badImage"}
    for name, rule in switches.items():
        assert rule["type"] == "boolean", f"{name} is declared {rule['type']}"
    assert SCHEMA["properties"]["failure"]["additionalProperties"] is False, (
        "without this, --set failure.crashloop=true asks for nothing and installs a healthy pod"
    )


def test_a_typo_in_a_switch_name_cannot_be_accepted_anywhere() -> None:
    """additionalProperties must be closed at every level, not only at the one that was noticed."""

    def closed(node: dict[str, Any], where: str) -> None:
        if node.get("type") == "object" or "properties" in node:
            assert node.get("additionalProperties") is False, f"{where} accepts unknown keys"
        for key, child in node.get("properties", {}).items():
            if isinstance(child, dict) and "$ref" not in child:
                closed(child, f"{where}.{key}")

    closed(SCHEMA, "values")
    for name, definition in SCHEMA.get("definitions", {}).items():
        closed(definition, f"definitions.{name}")


def test_the_digest_pattern_accepts_a_digest_and_refuses_a_word() -> None:
    """`busybox@notadigest` rendered without complaint before this pattern existed."""
    pattern = re.compile(SCHEMA["properties"]["image"]["properties"]["digest"]["pattern"])
    assert pattern.match("")
    assert pattern.match("sha256:" + "a" * 64)
    assert not pattern.match("notadigest")
    assert not pattern.match("sha256:" + "a" * 63)
    assert not pattern.match("sha256:" + "A" * 64), "registries write digests in lower case"


def test_the_pull_policy_is_an_enum_of_the_three_kubernetes_accepts() -> None:
    assert set(SCHEMA["properties"]["image"]["properties"]["pullPolicy"]["enum"]) == {
        "Always",
        "IfNotPresent",
        "Never",
    }


def test_the_pull_policy_renders_quoted() -> None:
    """Unquoted, `y` becomes a YAML boolean and the API server refuses the whole object."""
    assert "imagePullPolicy: {{ .Values.image.pullPolicy | quote }}" in rendered_templates()


def test_every_failure_switch_is_read_by_a_template() -> None:
    """A switch nothing reads is a switch that silently does nothing when it is set."""
    text = rendered_templates()
    for switch in values()["failure"]:
        assert f".Values.failure.{switch}" in text, f"failure.{switch} is read by no template"


def test_every_probe_value_the_file_declares_is_read_by_a_template() -> None:
    """values.yaml advertised /healthz and /readyz while the template asked for neither."""
    text = rendered_templates()
    for path in sorted(paths(values()["probes"], "probes")):
        assert f".Values.{path}" in text, f"{path} is documented in values.yaml and read by nothing"


def test_asking_for_two_failures_at_once_is_refused() -> None:
    """The switches are not independent, and the guard counts rather than naming a pair.

    A crash loop exits before an allocation starts or a readiness file is written, and a
    container whose image never pulled can do none of the three. Whichever switch wins, the
    caller measures a failure they did not ask for, so every combination is refused and not
    only the pair somebody happened to think of.
    """
    text = rendered_templates()
    assert "range $name, $on := .Values.failure" in text, (
        "the guard names specific switches, so a combination nobody listed renders silently"
    )
    assert "fail " in text, "the guard has to stop the render, not warn in a comment"


def test_the_liveness_path_is_one_this_container_serves() -> None:
    """A 404 is a failed liveness probe, and a failed liveness probe restarts a working process."""
    assert values()["probes"]["liveness"]["path"] == "/"
