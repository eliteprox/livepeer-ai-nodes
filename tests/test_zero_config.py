"""Zero-config sign-in defaults and orchestrator resolution."""

from pathlib import Path

import pytest

from nodes.pymthouse_auth import PymtHouseAuthError, load_settings_from_env
from nodes.pymthouse_session import discovery_url_from_env, offerings_url_from_env
from nodes.stream.credentials import resolve_network_config


def test_empty_env_uses_public_defaults():
    settings = load_settings_from_env({})

    assert settings.issuer_url == "https://pymthouse.com/api/v1/oidc"
    assert settings.client_id == "app_444013b9d984f62f5572a282"


def test_env_overrides_win_including_client_id_alias_order():
    primary = load_settings_from_env({
        "PYMTHOUSE_ISSUER_URL": "https://staging.example/api/v1/oidc/",
        "COMFYPEER_PUBLIC_CLIENT_ID": "app_primary",
        "PYMTHOUSE_PUBLIC_CLIENT_ID": "app_secondary",
        "PYMTHOUSE_CLIENT_ID": "app_legacy",
    })
    assert primary.issuer_url == "https://staging.example/api/v1/oidc"
    assert primary.client_id == "app_primary"

    secondary = load_settings_from_env({
        "PYMTHOUSE_PUBLIC_CLIENT_ID": "app_secondary",
        "PYMTHOUSE_CLIENT_ID": "app_legacy",
    })
    assert secondary.client_id == "app_secondary"
    assert secondary.issuer_url == "https://pymthouse.com/api/v1/oidc"

    legacy = load_settings_from_env({"PYMTHOUSE_CLIENT_ID": "app_legacy"})
    assert legacy.client_id == "app_legacy"


def test_blank_issuer_override_raises():
    with pytest.raises(PymtHouseAuthError) as caught:
        load_settings_from_env({
            "PYMTHOUSE_ISSUER_URL": "   ",
            "COMFYPEER_PUBLIC_CLIENT_ID": "app_primary",
        })

    assert str(caught.value) == (
        "Set PYMTHOUSE_ISSUER_URL and COMFYPEER_PUBLIC_CLIENT_ID before signing in."
    )


def test_explicit_orchestrator_wins_over_discovery():
    orchestrator, _signer = resolve_network_config(
        "https://explicit.example:8936",
        "",
        env={"ORCHESTRATOR_URL": "https://env.example:8936"},
        discovery=[{
            "address": "https://discovered.example:8936",
            "runners": [{"capacity_available": 2}],
        }],
        signed_in=True,
    )

    assert orchestrator == "https://explicit.example:8936"


def test_orchestrator_env_wins_over_discovery_when_node_field_is_empty():
    orchestrator, _signer = resolve_network_config(
        "",
        "",
        env={"ORCHESTRATOR_URL": "https://env.example:8936"},
        discovery=[{
            "address": "https://discovered.example:8936",
            "runners": [{"capacity_available": 3}],
        }],
        signed_in=True,
    )

    assert orchestrator == "https://env.example:8936"


def test_signed_in_session_picks_https_orchestrator_from_discovery():
    orchestrator, _signer = resolve_network_config(
        "",
        "",
        env={},
        discovery=[
            {"address": "http://insecure.example:8936", "runners": [{"capacity_available": 5}]},
            {"address": "https://busy.example:8936", "runners": [{"capacity_available": 0}]},
            {"address": "https://ready.example:8936", "runners": [{"capacity_available": 1}]},
        ],
        signed_in=True,
    )

    assert orchestrator == "https://ready.example:8936"


def test_empty_discovery_raises():
    with pytest.raises(ValueError) as caught:
        resolve_network_config("", "", env={}, discovery=[], signed_in=True)

    assert str(caught.value) == "No orchestrator available right now, try again"


def test_personal_orchestrator_host_is_not_a_default():
    import nodes.stream.credentials as credentials

    source = Path(credentials.__file__).read_text()
    assert not hasattr(credentials, "DEFAULT_ORCH_URL")
    assert "hky.eliteencoder.net" not in source

    try:
        resolved = resolve_network_config("", "", env={}, discovery=None, signed_in=False)
    except ValueError as exc:
        assert "hky.eliteencoder.net" not in str(exc)
    else:
        assert "hky.eliteencoder.net" not in resolved[0]


def test_localhost_placeholder_is_not_an_explicit_override():
    discovery = [{"address": "https://discovered.example:8936", "runners": []}]

    orchestrator, _signer = resolve_network_config(
        "https://localhost:8935",
        "",
        env={},
        discovery=discovery,
        signed_in=True,
    )

    assert orchestrator == "https://discovered.example:8936"


def test_discovery_without_capacity_uses_the_first_https_address():
    orchestrator, _signer = resolve_network_config(
        "",
        "",
        env={},
        discovery={
            "orchestrators": [
                {"address": "http://insecure.example:8936", "runners": [{"capacity_available": 4}]},
                {"address": "https://first.example:8936", "runners": [{"capacity_available": 0}]},
                {"address": "https://second.example:8936", "runners": []},
            ],
        },
        signed_in=True,
    )

    assert orchestrator == "https://first.example:8936"


def test_catalog_urls_are_overrides_not_preview_hosts():
    assert discovery_url_from_env({}) == ""
    assert offerings_url_from_env({}) == ""
    assert discovery_url_from_env({"COMFYPEER_DISCOVERY_URL": "   "}) == ""
    assert discovery_url_from_env({
        "COMFYPEER_DISCOVERY_URL": "https://staging.example/discovery",
    }) == "https://staging.example/discovery"
    assert offerings_url_from_env({
        "COMFYPEER_OFFERINGS_URL": "https://staging.example/v1/offerings",
    }) == "https://staging.example/v1/offerings"

    session_source = Path(discovery_url_from_env.__code__.co_filename).read_text()
    remote_source = (Path(discovery_url_from_env.__code__.co_filename).parent / "creative_remote.py").read_text()
    assert "railway.app" not in session_source
    assert "railway.app" not in remote_source


def test_blank_client_id_override_raises():
    with pytest.raises(PymtHouseAuthError) as caught:
        load_settings_from_env({"COMFYPEER_PUBLIC_CLIENT_ID": "  "})

    assert str(caught.value) == (
        "Set PYMTHOUSE_ISSUER_URL and COMFYPEER_PUBLIC_CLIENT_ID before signing in."
    )
