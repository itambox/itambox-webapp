"""U5/U7: contract tests for the declarative capability registry substrate.

These exercise the registry in isolation with synthetic entries. The shipped
declarations are asserted separately in ``test_capability_slices.py`` so a
defect in the substrate and a defect in a domain declaration never look alike.
"""

from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

from itambox.capabilities import (
    ACTIVATION_MODES,
    ACTIVATION_SOURCES,
    ALWAYS_ON,
    BETA,
    CAPABILITY_REGISTRY_DOC_URL,
    ENABLED,
    EXPERIMENTAL,
    MATURITIES,
    OPT_IN,
    SOURCE_ALWAYS,
    SOURCE_CONFIGURED,
    SOURCE_OBJECT_ENABLED,
    SOURCE_OPERATOR_FLAG,
    STABLE,
    ActivationState,
    Capability,
    CapabilityRegistry,
    DuplicateCapability,
    ProbeError,
    UnknownCapability,
)


def make_capability(**overrides):
    """A minimal valid Stable entry; override any field for the case under test."""
    fields = {
        "key": "demo.core",
        "title": "Demo",
        "owning_area": "area:operations",
        "maturity": STABLE,
        "security_critical": False,
        "activation": ALWAYS_ON,
        "activation_source": SOURCE_ALWAYS,
        "activation_probe": None,
        "owns": ("extras.Tag",),
        "docs_url": CAPABILITY_REGISTRY_DOC_URL,
        "limitations": (),
        "contract_version": 1,
    }
    fields.update(overrides)
    return Capability(**fields)


def beta_capability(active=True, value_present=True, **overrides):
    fields = {
        "key": "demo.beta",
        "maturity": BETA,
        "activation": ENABLED,
        "activation_source": SOURCE_OBJECT_ENABLED,
        "activation_probe": lambda: ActivationState(active=active, value_present=value_present),
        "owns": ("extras.Bookmark",),
        "limitations": ("Payload shape is not frozen.",),
    }
    fields.update(overrides)
    return make_capability(**fields)


class TestStableSurfaces:
    """The vocabularies are a published surface: closed, ordered, and spelled once."""

    def test_the_vocabularies_are_closed(self):
        assert MATURITIES == (STABLE, BETA, EXPERIMENTAL)
        assert ACTIVATION_MODES == (ALWAYS_ON, ENABLED, OPT_IN)
        assert ACTIVATION_SOURCES == (
            SOURCE_ALWAYS,
            SOURCE_CONFIGURED,
            SOURCE_OBJECT_ENABLED,
            SOURCE_OPERATOR_FLAG,
        )

    def test_the_source_names_are_the_documented_spellings(self):
        assert (SOURCE_ALWAYS, SOURCE_CONFIGURED, SOURCE_OBJECT_ENABLED, SOURCE_OPERATOR_FLAG) == (
            "always",
            "configured",
            "object-enabled",
            "operator-flag",
        )

    def test_maturity_names_are_the_documented_spellings(self):
        assert (STABLE, BETA, EXPERIMENTAL) == ("stable", "beta", "experimental")

    def test_activation_mode_names_are_the_documented_spellings(self):
        assert (ALWAYS_ON, ENABLED, OPT_IN) == ("always-on", "enabled", "opt-in")

    def test_the_entry_declares_exactly_the_contract_fields(self):
        assert tuple(field for field in make_capability().__dataclass_fields__) == (
            "key",
            "title",
            "owning_area",
            "maturity",
            "security_critical",
            "activation",
            "activation_probe",
            "activation_source",
            "owns",
            "docs_url",
            "limitations",
            "contract_version",
        )


class TestEntryValidation:
    def test_an_unknown_maturity_is_refused(self):
        with pytest.raises(ValueError, match="maturity"):
            make_capability(maturity="preview")

    def test_an_unknown_activation_mode_is_refused(self):
        with pytest.raises(ValueError, match="activation"):
            beta_capability(activation="sometimes")

    def test_an_unknown_activation_source_is_refused(self):
        with pytest.raises(ValueError, match="activation_source"):
            beta_capability(activation_source="vibes")

    def test_a_stable_capability_is_always_on(self):
        with pytest.raises(ValueError, match="stable"):
            make_capability(activation=OPT_IN, activation_source=SOURCE_OPERATOR_FLAG)

    def test_a_stable_capability_carries_no_probe(self):
        with pytest.raises(ValueError, match="probe"):
            make_capability(activation_probe=lambda: ActivationState(True, True))

    def test_a_non_stable_capability_needs_a_probe(self):
        with pytest.raises(ValueError, match="probe"):
            beta_capability(activation_probe=None)

    def test_a_non_stable_capability_may_not_be_always_on(self):
        with pytest.raises(ValueError, match="always-on"):
            beta_capability(activation=ALWAYS_ON, activation_source=SOURCE_ALWAYS)

    def test_a_probe_must_be_callable(self):
        with pytest.raises(TypeError, match="probe"):
            beta_capability(activation_probe="core.features.module_maturity")

    def test_a_security_critical_capability_may_not_be_deactivatable(self):
        with pytest.raises(ValueError, match="security_critical"):
            beta_capability(security_critical=True)

    def test_a_security_critical_stable_capability_is_accepted(self):
        assert make_capability(security_critical=True).security_critical is True

    def test_the_key_is_a_lowercase_dotted_identifier(self):
        with pytest.raises(ValueError, match="key"):
            make_capability(key="Demo.Core")

    def test_the_key_is_dotted(self):
        with pytest.raises(ValueError, match="key"):
            make_capability(key="demo")

    def test_a_title_is_required(self):
        with pytest.raises(ValueError, match="title"):
            make_capability(title="")

    def test_an_owning_area_must_be_an_area_label(self):
        with pytest.raises(ValueError, match="owning_area"):
            make_capability(owning_area="operations")

    def test_a_docs_url_is_required(self):
        with pytest.raises(ValueError, match="docs_url"):
            make_capability(docs_url="")

    def test_the_contract_version_is_a_positive_integer(self):
        with pytest.raises(ValueError, match="contract_version"):
            make_capability(contract_version=0)

    def test_owned_references_are_dotted_strings(self):
        with pytest.raises(TypeError, match="owns"):
            make_capability(owns=("extras.Tag", 4))

    def test_owned_references_must_be_dotted(self):
        with pytest.raises(ValueError, match="owns"):
            make_capability(owns=("extras",))

    def test_at_least_one_owned_reference_is_required(self):
        with pytest.raises(ValueError, match="owns"):
            make_capability(owns=())

    def test_a_non_stable_capability_declares_its_limitations(self):
        with pytest.raises(ValueError, match="limitations"):
            beta_capability(limitations=())

    def test_limitations_are_strings(self):
        with pytest.raises(TypeError, match="limitations"):
            beta_capability(limitations=(7,))

    def test_an_entry_is_immutable(self):
        capability = make_capability()
        with pytest.raises(FrozenInstanceError):
            capability.maturity = BETA


class TestRegistration:
    def test_registration_is_keyed_and_sorted(self):
        registry = CapabilityRegistry()
        registry.register(beta_capability())
        registry.register(make_capability())
        assert [entry.key for entry in registry.all()] == ["demo.beta", "demo.core"]
        assert registry.keys() == ("demo.beta", "demo.core")
        assert len(registry) == 2
        assert "demo.core" in registry
        assert "demo.absent" not in registry

    def test_a_duplicate_key_is_refused(self):
        registry = CapabilityRegistry()
        registry.register(make_capability())
        with pytest.raises(DuplicateCapability, match="demo.core"):
            registry.register(make_capability(owns=("extras.Dashboard",)))

    def test_two_capabilities_may_not_own_the_same_reference(self):
        registry = CapabilityRegistry()
        registry.register(make_capability())
        with pytest.raises(DuplicateCapability, match="extras.Tag"):
            registry.register(make_capability(key="demo.other"))

    def test_a_refused_registration_leaves_the_registry_unchanged(self):
        registry = CapabilityRegistry()
        registry.register(make_capability())
        with pytest.raises(DuplicateCapability):
            registry.register(make_capability(key="demo.other"))
        assert registry.keys() == ("demo.core",)
        assert registry.owner_of("extras.Tag").key == "demo.core"

    def test_an_unknown_key_raises(self):
        with pytest.raises(UnknownCapability, match="demo.missing"):
            CapabilityRegistry().get("demo.missing")

    def test_registering_a_non_capability_is_refused(self):
        with pytest.raises(TypeError):
            CapabilityRegistry().register(object())


class TestMultipartRegistration:
    """``register_all`` is how an application declares more than one slice.

    ``AppConfig.ready`` runs again whenever a test swaps ``INSTALLED_APPS``, and
    a declaration that failed partway through must be *finishable* on the next
    run. Guarding on the first key only would freeze the registry in the
    half-registered state that the failure produced.
    """

    def test_a_multipart_declaration_registers_every_entry(self):
        registry = CapabilityRegistry()
        registry.register_all((make_capability(), beta_capability()))
        assert registry.keys() == ("demo.beta", "demo.core")

    def test_re_declaring_the_same_entries_is_a_no_op(self):
        registry = CapabilityRegistry()
        registry.register_all((make_capability(), beta_capability()))
        registry.register_all((make_capability(), beta_capability()))
        assert registry.keys() == ("demo.beta", "demo.core")

    def test_a_partial_declaration_is_completed_by_the_next_run(self):
        """The defect this replaces: a first-key guard hides the missing tail."""
        registry = CapabilityRegistry()
        registry.register(make_capability())
        registry.register_all((make_capability(), beta_capability()))
        assert registry.keys() == ("demo.beta", "demo.core")

    def test_a_key_re_declared_with_a_different_shape_is_still_a_collision(self):
        registry = CapabilityRegistry()
        registry.register_all((make_capability(),))
        with pytest.raises(DuplicateCapability, match="demo.core"):
            registry.register_all((make_capability(title="Something else"),))

    def test_a_probe_appearing_or_vanishing_is_a_different_shape(self):
        registry = CapabilityRegistry()
        registry.register_all((beta_capability(),))
        with pytest.raises(DuplicateCapability, match="demo.beta"):
            registry.register_all(
                (
                    beta_capability(
                        maturity=STABLE,
                        activation=ALWAYS_ON,
                        activation_source=SOURCE_ALWAYS,
                        activation_probe=None,
                        limitations=(),
                    ),
                )
            )

    def test_two_runs_build_distinct_probes_and_still_compare_equal(self):
        """Each ``ready()`` builds fresh closures; identity cannot be the test."""
        first, second = beta_capability(), beta_capability()
        assert first.activation_probe is not second.activation_probe
        assert first.declaration() == second.declaration()

    def test_a_declaration_omits_the_probe_object_itself(self):
        assert beta_capability().activation_probe not in beta_capability().declaration()


class TestOwnership:
    def test_the_owner_of_a_reference_is_resolvable(self):
        registry = CapabilityRegistry()
        capability = make_capability()
        registry.register(capability)
        assert registry.owner_of("extras.Tag") is capability
        assert registry.owner_of("extras.Missing") is None

    def test_owns_is_never_evaluated_at_registration(self):
        """Domain references stay dotted strings; nothing is imported to register."""
        registry = CapabilityRegistry()
        registry.register(make_capability(owns=("nowhere.NotAModel",)))
        assert registry.get("demo.core").owns == ("nowhere.NotAModel",)

    def test_an_unresolvable_reference_is_reported_not_raised(self):
        registry = CapabilityRegistry()
        registry.register(make_capability(owns=("nowhere.NotAModel",)))
        unresolved = registry.unresolved_references()
        assert [(row.key, row.reference) for row in unresolved] == [("demo.core", "nowhere.NotAModel")]
        assert unresolved[0].reason

    def test_a_resolver_failure_publishes_only_its_type(self):
        registry = CapabilityRegistry()
        registry.register(make_capability(owns=("extras.Tag",)))

        with patch("itambox.capabilities.resolve_reference", side_effect=RuntimeError("credential=secret")):
            unresolved = registry.unresolved_references()

        assert [(row.key, row.reference, row.reason) for row in unresolved] == [
            ("demo.core", "extras.Tag", "RuntimeError")
        ]
        assert "credential=secret" not in repr(unresolved)

    def test_a_resolvable_model_reference_is_not_reported(self):
        registry = CapabilityRegistry()
        registry.register(make_capability(owns=("extras.Tag",)))
        assert registry.unresolved_references() == ()

    def test_a_resolvable_module_reference_is_not_reported(self):
        registry = CapabilityRegistry()
        registry.register(make_capability(owns=("itambox.capabilities",)))
        assert registry.unresolved_references() == ()


class TestActivation:
    def test_a_stable_capability_is_always_active(self):
        registry = CapabilityRegistry()
        registry.register(make_capability())
        state = registry.state("demo.core")
        assert (state.active, state.value_present) == (True, True)
        assert registry.is_active("demo.core") is True

    def test_a_beta_enabled_capability_follows_its_probe(self):
        registry = CapabilityRegistry()
        registry.register(beta_capability(active=False, value_present=False))
        assert registry.is_active("demo.beta") is False

    def test_an_opt_in_capability_is_inert_by_default(self):
        registry = CapabilityRegistry()
        registry.register(
            beta_capability(
                key="demo.optin",
                activation=OPT_IN,
                activation_source=SOURCE_OPERATOR_FLAG,
                active=False,
                value_present=False,
            )
        )
        assert registry.is_active("demo.optin") is False

    def test_an_experimental_capability_is_inert_by_default(self):
        registry = CapabilityRegistry()
        registry.register(
            beta_capability(
                key="demo.experiment",
                maturity=EXPERIMENTAL,
                activation=OPT_IN,
                activation_source=SOURCE_OPERATOR_FLAG,
                active=False,
                value_present=False,
            )
        )
        assert registry.is_active("demo.experiment") is False

    def test_a_failing_probe_fails_closed(self):
        def explode():
            raise RuntimeError("database is gone")

        registry = CapabilityRegistry()
        registry.register(beta_capability(activation_probe=explode))
        state = registry.state("demo.beta")
        assert state.active is False
        assert state.value_present is False
        assert state.probe_error == "RuntimeError"
        assert "database is gone" not in state.probe_error

    def test_a_probe_returning_the_wrong_type_is_a_programming_error(self):
        registry = CapabilityRegistry()
        registry.register(beta_capability(activation_probe=lambda: True))
        with pytest.raises(ProbeError):
            registry.state("demo.beta")

    def test_is_active_of_an_unknown_key_raises(self):
        with pytest.raises(UnknownCapability):
            CapabilityRegistry().is_active("demo.nope")

    def test_evaluation_is_not_memoised(self):
        """State is read live: a deactivated capability must not stay active."""
        calls = []

        def probe():
            calls.append(1)
            return ActivationState(active=len(calls) < 2, value_present=True)

        registry = CapabilityRegistry()
        registry.register(beta_capability(activation_probe=probe))
        assert registry.is_active("demo.beta") is True
        assert registry.is_active("demo.beta") is False


class TestDiagnostics:
    def test_a_row_reports_class_mode_state_source_and_value_presence(self):
        registry = CapabilityRegistry()
        registry.register(beta_capability(active=True, value_present=True))
        (row,) = registry.diagnostics()
        assert row["key"] == "demo.beta"
        assert row["title"] == "Demo"
        assert row["owning_area"] == "area:operations"
        assert row["maturity"] == BETA
        assert row["activation"] == ENABLED
        assert row["activation_source"] == SOURCE_OBJECT_ENABLED
        assert row["active"] is True
        assert row["value_present"] is True
        assert row["probe_error"] == ""
        assert row["contract_version"] == 1
        assert row["security_critical"] is False

    def test_a_row_carries_no_field_beyond_the_documented_set(self):
        registry = CapabilityRegistry()
        registry.register(beta_capability())
        (row,) = registry.diagnostics()
        assert set(row) == {
            "key",
            "title",
            "owning_area",
            "maturity",
            "security_critical",
            "activation",
            "activation_source",
            "active",
            "value_present",
            "probe_error",
            "docs_url",
            "contract_version",
        }

    def test_rows_are_sorted_by_key(self):
        registry = CapabilityRegistry()
        registry.register(beta_capability())
        registry.register(make_capability())
        assert [row["key"] for row in registry.diagnostics()] == ["demo.beta", "demo.core"]

    def test_a_row_never_carries_a_probe_supplied_value(self):
        """The probe reports presence, never the value: no secret can reach a row."""
        registry = CapabilityRegistry()
        secret = "s3cr3t-token-value"
        registry.register(
            beta_capability(activation_probe=lambda: ActivationState(True, bool(secret))),
        )
        (row,) = registry.diagnostics()
        assert row["value_present"] is True
        assert secret not in repr(row)
        assert all(isinstance(value, (str, bool, int)) for value in row.values())

    def test_a_failing_probe_is_visible_without_its_message(self):
        def explode():
            raise ValueError("token=hunter2")

        registry = CapabilityRegistry()
        registry.register(beta_capability(activation_probe=explode))
        (row,) = registry.diagnostics()
        assert row["active"] is False
        assert row["probe_error"] == "ValueError"
        assert "hunter2" not in repr(row)


class TestActivationState:
    def test_the_state_carries_no_free_text_channel(self):
        state = ActivationState(True, True)
        assert state.active is True
        assert state.value_present is True
        assert state.probe_error == ""

    def test_the_state_is_immutable(self):
        state = ActivationState(True, True)
        with pytest.raises(FrozenInstanceError):
            state.active = False

    def test_the_state_rejects_non_boolean_flags(self):
        with pytest.raises(TypeError):
            ActivationState("yes", True)

    def test_the_state_rejects_a_non_string_probe_error(self):
        with pytest.raises(TypeError):
            ActivationState(False, False, probe_error=ValueError("boom"))


class TestConfiguredSourceSemantics:
    """A configured/object-enabled deployment stays active from what it already has."""

    @pytest.mark.parametrize("source", [SOURCE_CONFIGURED, SOURCE_OBJECT_ENABLED])
    def test_presence_of_existing_state_keeps_a_beta_enabled_capability_active(self, source):
        registry = CapabilityRegistry()
        registry.register(
            beta_capability(
                activation=ENABLED,
                activation_source=source,
                active=True,
                value_present=True,
            )
        )
        assert registry.is_active("demo.beta") is True

    @pytest.mark.parametrize("source", [SOURCE_CONFIGURED, SOURCE_OBJECT_ENABLED])
    def test_absence_of_existing_state_gives_the_documented_inactive_state(self, source):
        registry = CapabilityRegistry()
        registry.register(
            beta_capability(
                activation=ENABLED,
                activation_source=source,
                active=False,
                value_present=False,
            )
        )
        state = registry.state("demo.beta")
        assert (state.active, state.value_present) == (False, False)
