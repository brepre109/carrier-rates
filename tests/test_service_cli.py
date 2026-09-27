import json
from decimal import Decimal

import pytest

from carrier_rates import RateQuote, cheapest, quote_all
from carrier_rates.config import credentials_from_env, load_dotenv, resolve_env
from carrier_rates.errors import ConfigError, CarrierRatesError
from carrier_rates.service import build_client, sort_quotes
from carrier_rates.cli import main
from conftest import MixedFixtures


def q(carrier, code, total, days=None, currency="USD"):
    return RateQuote(carrier, code, code, Decimal(total), currency, False, transit_days=days)


def test_cheapest_selection():
    quotes = [q("ups", "03", "13.48", None), q("fedex", "G", "13.48", 5), q("fedex", "2D", "38.10", 2)]
    assert cheapest(quotes).service_code == "G"  # tie on price -> known faster transit wins
    assert cheapest(quotes, max_transit_days=2).service_code == "2D"
    assert cheapest(quotes, max_transit_days=0) is None
    assert cheapest([]) is None
    assert cheapest([q("a", "x", "5", currency="CAD"), q("b", "y", "9")], currency="USD").carrier == "b"
    assert [x.total_charge for x in sort_quotes(reversed(quotes))] == [Decimal("13.48")] * 2 + [Decimal("38.10")]


def test_quote_all_isolates_carrier_failures(fedex, ups, shipment):
    ups.transport = MixedFixtures({"ups_oauth": ["ups_oauth_denied"]})
    result = quote_all(shipment, {"fedex": fedex, "ups": ups})
    assert {x.carrier for x in result.quotes} == {"fedex"}
    assert "ups" in result.errors
    assert result.cheapest.total_charge == Decimal("13.95")
    assert result.quotes == sorted(result.quotes, key=lambda x: x.total_charge)


def test_env_resolution():
    assert resolve_env(None) == "sandbox"
    assert resolve_env("Production") == "production"
    with pytest.raises(ConfigError):
        resolve_env("prod")


def test_credentials_from_env():
    assert credentials_from_env("ups", {}) is None
    assert credentials_from_env("ups", {"UPS_CLIENT_ID": "a"}) is None
    creds = credentials_from_env("ups", {"UPS_CLIENT_ID": "a", "UPS_CLIENT_SECRET": "s3cr3t-value"})
    assert creds.account_number is None and "s3cr3t-value" not in repr(creds)


def test_build_client_defaults_to_fixtures_without_creds():
    client, source = build_client("fedex", environ={})
    assert source == "fixtures" and client.env == "sandbox"
    with pytest.raises(CarrierRatesError, match="live credentials"):
        build_client("ups", environ={}, record_dir="x")
    live, source = build_client("ups", environ={"UPS_CLIENT_ID": "a", "UPS_CLIENT_SECRET": "b",
                                                "CARRIER_RATES_ENV": "production"})
    assert source == "live" and live.base_url == "https://onlinetools.ups.com"
    with pytest.raises(CarrierRatesError):
        build_client("dhl", environ={})


def test_load_dotenv(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# comment\nexport UPS_CLIENT_ID='abc'\nUPS_CLIENT_SECRET=def\nKEEP=new\n")
    env = {"KEEP": "old"}
    load_dotenv(f, env)
    assert env == {"KEEP": "old", "UPS_CLIENT_ID": "abc", "UPS_CLIENT_SECRET": "def"}


@pytest.fixture
def no_creds(monkeypatch, tmp_path):
    for k in ("FEDEX_CLIENT_ID", "FEDEX_CLIENT_SECRET", "UPS_CLIENT_ID", "UPS_CLIENT_SECRET", "CARRIER_RATES_ENV"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)  # no stray ./.env


def test_cli_table(no_creds, capsys):
    rc = main(["quote", "--from", "90210", "--to", "10001", "--weight", "5", "--dims", "12x10x8"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert "replayed from fixtures" in err
    lines = out.splitlines()
    assert lines[0].split()[:3] == ["CARRIER", "SERVICE", "TOTAL"]
    assert lines[2].startswith("*") and "UPS Ground" in lines[2]
    assert "cheapest: UPS Ground (ups, account rate) 13.48 USD" in out


def test_cli_json_single_carrier(no_creds, capsys):
    rc = main(["quote", "--from", "90210", "--to", "10001", "--weight", "2.3", "--weight-unit", "kg",
               "--dims", "30x25x20", "--dim-unit", "cm", "--carrier", "fedex", "--fixtures", "--json",
               "--list-rates", "--max-days", "2"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["sources"] == {"fedex": "fixtures"}
    totals = [Decimal(x["total_charge"]) for x in data["quotes"]]
    assert totals == sorted(totals) and len(totals) == 5
    assert data["cheapest"]["service_code"] == "FEDEX_2_DAY"
    assert data["errors"] == {}


def test_cli_bad_input(no_creds, capsys):
    assert main(["quote", "--from", "1", "--to", "2", "--weight", "5", "--dims", "12x10"]) == 2
    assert "LxWxH" in capsys.readouterr().err
    assert main(["quote", "--from", "1", "--to", "2", "--weight", "-1", "--dims", "1x1x1"]) == 2


def test_cli_reports_carrier_errors(no_creds, capsys, tmp_path):
    fixtures = tmp_path / "fx"
    fixtures.mkdir()
    (fixtures / "ups_oauth.json").write_text(json.dumps(
        {"status": 401, "body": {"response": {"errors": [{"code": "10401", "message": "ClientId is Invalid"}]}}}))
    rc = main(["quote", "--from", "1", "--to", "2", "--weight", "1", "--dims", "1x1x1", "--carrier", "ups",
               "--fixture-dir", str(fixtures)])
    _, err = capsys.readouterr()
    assert rc == 1 and "ups: AuthError" in err and "10401" in err
