"""Product-owned ingress and healthcheck contracts."""

from pathlib import Path

import yaml


def test_production_routes_preview_hosts_with_dns_challenge_and_proxy_only_credentials() -> None:
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "infra/compose.production.yml").read_text())
    gateway = compose["services"]["gateway"]
    assert gateway["environment"]["PLAYGROUND_PREVIEW_DOMAIN"] == "${PLAYGROUND_DOMAIN}"
    assert not any("CLOUDFLARE" in key or key.startswith("CF_") for key in gateway["environment"])
    assert "PLAYGROUND_BOXIE_URL" in gateway["environment"]
    assert "PLAYGROUND_SANDBOX_RUNTIME" not in gateway["environment"]
    proxy = compose["services"]["proxy"]
    assert (
        "--certificatesresolvers.preview.acme.dnschallenge.provider=cloudflare" in proxy["command"]
    )
    assert not any("httpchallenge" in argument for argument in proxy["command"])
    assert "env_file" not in proxy
    assert set(proxy["environment"]) == {"PLAYGROUND_DOMAIN", "CF_DNS_API_TOKEN"}
    assert "CLOUDFLARE_DNS_API_TOKEN" in proxy["environment"]["CF_DNS_API_TOKEN"]

    routes = yaml.safe_load((root / "infra/routes.yml").read_text())
    router = routes["http"]["routers"]["playground"]
    assert router["rule"] == (
        'Host(`{{ env "PLAYGROUND_DOMAIN" }}`) || '
        "HostRegexp(`^(pr-[1-9][0-9]*|[0-9a-f]{32})[.]"
        '{{ env "PLAYGROUND_DOMAIN" | replace "." "[.]" }}$`)'
    )
    assert router["entryPoints"] == ["websecure"]
    assert router["tls"] == {
        "certResolver": "preview",
        "domains": [
            {"main": '{{ env "PLAYGROUND_DOMAIN" }}', "sans": ['*.{{ env "PLAYGROUND_DOMAIN" }}']}
        ],
    }
