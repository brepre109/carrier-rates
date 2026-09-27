# carrier-rates

Compare FedEx and UPS shipping rates from Python or the command line. Both carriers' answers come back in the same normalized shape, and the whole thing runs offline against sample data when you have no API keys.

```
$ carrier-rates quote --from 90210 --to 10001 --weight 5 --dims 12x10x8
note: fedex, ups replayed from fixtures (sample data, not live prices)
   CARRIER  SERVICE                       TOTAL  RATE     BILL WT  DAYS  DELIVERY    SURCHARGES
-  -------  ------------------------  ---------  -------  -------  ----  ----------  ----------
*  UPS      UPS Ground                13.48 USD  account  7.0 LBS     -  -                 2.02
   FEDEX    FedEx Ground              13.95 USD  account   7.0 LB     5  -                 2.55
   UPS      UPS 3 Day Select          27.90 USD  account  7.0 LBS     3  -                    -
   UPS      UPS 2nd Day Air           37.66 USD  account  7.0 LBS     2  -                    -
   FEDEX    FedEx 2Day                38.10 USD  account   7.0 LB     2  2026-09-30        5.10
   FEDEX    FedEx Standard Overnight  89.40 USD  list      7.0 LB     1  2026-09-29           -
   UPS      UPS Next Day Air          96.30 USD  list     7.0 LBS     1  -                11.02

cheapest: UPS Ground (ups, account rate) 13.48 USD
```

(The prices above are invented. They come from the hand-written fixtures bundled with the package.)

## Features

- **FedEx REST Rate API.** Gets an OAuth `client_credentials` token from `POST /oauth/token`, then calls `POST /rate/v1/rates/quotes` and asks for both `ACCOUNT` and `LIST` rates plus transit times.
- **UPS OAuth Rating API.** Gets a token from `POST /security/v1/oauth/token` using HTTP Basic auth, then calls `POST /api/rating/{version}/{Shop|Rate|Shoptimeintransit|Ratetimeintransit}`. Negotiated rates are requested whenever a shipper number is configured.
- **Normalized model.** Every result is a `RateQuote` with: carrier, service code and name, `total_charge` as a `Decimal`, currency, billable weight and unit, transit days, delivery date (when the carrier returns one), a list-rate vs account-rate flag, and the itemized surcharges. Responses are parsed with `parse_float=Decimal`, so money never passes through binary floats.
- **Normalized inputs.** `Address`, `Package` and `Shipment` work internally in pounds and inches. Helpers convert from kg, oz, g, cm and mm, parse `12x10x8` strings and estimate dimensional weight.
- **Token caching.** Tokens are kept until 60 s before `expires_in` runs out. If the carrier rejects a cached token with a 401, the token is dropped and the request is retried once.
- **Sandbox by default.** Requests go to `apis-sandbox.fedex.com` and `wwwcie.ups.com` unless `CARRIER_RATES_ENV=production` is set explicitly. Any other value is an error.
- **Pluggable transport.** Every HTTP call goes through `Transport.send()`:
  - `UrllibTransport` makes real network calls using only the standard library.
  - `FixtureTransport` replays JSON fixtures.
  - `RecordingTransport` wraps a live transport and saves each response as a *sanitized* fixture.
- **Normalized errors.** Rejected credentials raise `AuthError`, carrier validation failures raise `ValidationError` and other HTTP errors raise `CarrierAPIError`. Each carries the list of error codes and messages the carrier returned. Network failures raise `TransportError`, and configuration problems raise `ConfigError`.
- **Multi-carrier orchestration.** `quote_all()` gets quotes from every configured carrier. If one carrier fails, its error is recorded and the other carrier's quotes are still returned. `cheapest()` picks the lowest price and can optionally require a maximum transit time and a single currency.
- **No runtime dependencies.** The only extra package is pytest, for development.

## Quick start

Requires Python 3.10 or newer.

```bash
git clone <this repo> carrier-rates && cd carrier-rates
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'

pytest -q                                   # 45 tests, no network

# Offline demo. Fixtures are used automatically when no credentials are set.
carrier-rates quote --from 90210 --to 10001 --weight 5 --dims 12x10x8
carrier-rates quote --from 90210 --to 10001 --weight 2.3 --weight-unit kg \
    --dims 30x25x20 --dim-unit cm --carrier fedex --fixtures --json
carrier-rates quote --from 90210 --to 10001 --weight 5 --dims 12x10x8 \
    --list-rates --max-days 2               # show list rates too; cheapest within 2 days
```

### Live (sandbox) calls

```bash
cp .env.example .env    # fill in the keys from the FedEx / UPS developer portals
carrier-rates quote --from 90210 --to 10001 --weight 5 --dims 12x10x8 --residential
carrier-rates quote ... --record recorded/   # also save sanitized responses as fixtures
carrier-rates quote ... --fixture-dir recorded/   # replay them later, offline
```

Each carrier is used live only when both its `*_CLIENT_ID` and `*_CLIENT_SECRET` are set. A carrier without credentials falls back to fixtures, and the CLI prints a note on stderr when that happens. `--fixtures` forces fixtures for all carriers.

### Library use

```python
from carrier_rates import Address, Package, Shipment, quote_all
from carrier_rates.service import build_client

shipment = Shipment(
    origin=Address("90210", "US"),
    destination=Address("10001", "US", residential=True),
    packages=(Package.create(5, (12, 10, 8)),),
)
clients = {name: build_client(name)[0] for name in ("fedex", "ups")}
result = quote_all(shipment, clients)
print(result.cheapest, result.errors)
```

To test your own code, point a client at `FixtureTransport("path/to/fixtures")` or at any object that has a `send(HttpRequest) -> HttpResponse` method.

## Architecture

```
src/carrier_rates/
  models.py     Address / Package / Shipment inputs, RateQuote / Surcharge output
  units.py      lb/kg/oz, in/cm conversions, LxWxH parsing, dim-weight estimate
  config.py     sandbox/production base URLs, credentials from env, tiny .env loader
  auth.py       TokenCache (expiry with skew, injectable clock)
  transport.py  HttpRequest/HttpResponse, Urllib/Fixture/Recording transports, URL->fixture routing
  sanitize.py   key-based + literal-value redaction for recorded payloads
  base.py       CarrierClient: token lifecycle, retry-once on 401, HTTP status -> exception mapping
  fedex.py      FedEx request builder + response parser
  ups.py        UPS request builder + response parser
  service.py    build_client(), quote_all(), cheapest(), sort_quotes()
  cli.py        `carrier-rates quote`
  fixtures/     synthetic responses in the carriers' documented shapes
```

Each carrier client only has to implement four methods: `build_rate_request`, `parse_rate_response`, `rate_url` and `_fetch_token`. Everything else lives in `base.py`, so adding a third carrier means writing one more module.

The fixture file name is derived from the request URL (`/oauth/token` becomes `fedex_oauth`, `/api/rating/v2409/Shop` becomes `ups_shop`, and so on). That is why a directory written by `--record` can be replayed as-is with `--fixture-dir`.

## Notes on the carrier APIs

These are practical quirks the code handles. Check them against the current carrier docs before relying on them.

- **Token requests differ.** FedEx expects `client_id` and `client_secret` in the form body. UPS expects them as HTTP Basic auth, with only `grant_type=client_credentials` in the body. It also accepts an optional `x-merchant-id` header set to the shipper number.
- **Token lifetimes differ.** FedEx tokens last about an hour, UPS tokens about four hours. UPS sends `expires_in` as a **string** (for example `"14399"`), so it has to be converted before doing arithmetic with it.
- **UPS sends numbers as strings.** This applies to both requests and responses (`"Weight": "5"`, `"MonetaryValue": "13.48"`).
- **UPS response shape changes with the request option.** A single-service `Rate` call returns `RatedShipment` as an **object**, while `Shop` returns a list. `ItemizedCharges` can likewise be an object or a list. The parser accepts both.
- **UPS usually leaves `Service.Description` empty.** Service names are mapped from the code instead (`03` is Ground, `02` is 2nd Day Air, and so on).
- **UPS negotiated rates need two things.** The request must include both `Shipper.ShipperNumber` and `ShipmentRatingOptions.NegotiatedRatesIndicator = "Y"`. They come back under `NegotiatedRateCharges.TotalCharge`, while `TotalCharges` holds the published rate. If the account has no contract pricing, the two can be identical. That is worth checking before you assume you have a discount.
- **UPS residential flag.** `ResidentialAddressIndicator` works by being present; its value is ignored.
- **UPS transit times.** Plain `Shop` returns only `GuaranteedDelivery.BusinessDaysInTransit`, and only for guaranteed services, so Ground shows no transit time. The `Shoptimeintransit` option returns `TimeInTransit.ServiceSummary.EstimatedArrival` instead, but it needs `DeliveryTimeInformation` and `ShipmentTotalWeight` in the request. Use `UPSClient(transit=True)` to get it.
- **UPS error shape.** Errors come back as `{"response": {"errors": [{"code", "message"}]}}`. A bad or expired bearer token returns code `250002` with HTTP 401.
- **FedEx rate types.** FedEx returns one `ratedShipmentDetails` entry per rate type (`ACCOUNT` or `LIST`) for each service. The sandbox or an account without discounts may return only `LIST`. By default the client keeps the account rate when there is one and falls back to the list rate; `include_list_rates=True` keeps both.
- **FedEx dimensions must be whole inches.** Fractional values are rejected, so this library rounds each side up.
- **FedEx transit times.** Pass `rateRequestControlParameters.returnTransitTimes = true` to get them. The delivery date then appears in `commit.dateDetail.dayFormat` or `operationalDetail.deliveryDate`, and the transit time as an enum such as `THREE_DAYS`.
- **FedEx error shape.** Errors come back as `{"errors": [{"code", "message"}]}`, with dotted codes such as `NOT.AUTHORIZED.ERROR`.
- **Rating is read-only; shipping is not.** On both carriers the *shipping* endpoints create real, billable shipments in production. FedEx production Ship access also requires a label-validation step. This library only calls the rating endpoints.

## Tech stack

Python 3.10+, standard library only (`urllib`, `json`, `decimal`, `dataclasses`, `argparse`), and pytest for tests.

## Status / limitations

- The bundled fixtures are **hand-written and synthetic**. Their structure follows the carriers' documented response shapes, but every price in them is made up. The fixture transport returns the same response whatever the input, so `--fixtures` output does not change with origin, destination or weight.
- The live code paths are covered by tests only through mock transports and fixtures. Before relying on a live integration, run it against your own sandbox credentials. Sandbox prices are not real prices either.
- Supported inputs are domestic parcel shipments with one or more identical packages from the CLI, or any list of packages from the library. International customs data (commodities, invoice totals), freight/LTL, one-rate and flat-rate packaging, hazmat, and special services such as signature or insurance are not modelled.
- The only UPS surcharge codes given readable names are `375` (fuel) and `270` (residential). Others are shown as `UPS charge <code>`.
- Only one retry is attempted, and only after a 401. There is no backoff for rate limiting or 5xx errors.
- The dimensional-weight helper is an estimate (divisor 139). The billable weight reported by the carrier is authoritative.

## License

MIT. See [LICENSE](LICENSE).
