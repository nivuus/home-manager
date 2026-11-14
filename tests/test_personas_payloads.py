#!/usr/bin/env python3
"""Do the Personas Studio channels still speak the same wire format?

The integration absorbed the `rest_command: ha_ai_feedback` that used to live
in configuration.yaml. That block was removed: this file is the only place its
contract survives. If `feedback_payload()` drifts, nothing in Home Assistant
will say so -- the webhook answers 202 to any body, and the automation calling
it is fire-and-forget. Hence these byte-for-byte assertions.

These functions do not import Home Assistant, on purpose: that is what makes
them checkable by `make test`, which needs nothing but python3.

Run: python3 tests/test_personas_payloads.py
"""
import json
import pathlib
import sys

# Do not seed a __pycache__ in the deployed subtree: install.py copies it as is
# to the target, and Home Assistant would load the stale cache.
sys.dont_write_bytecode = True

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "stack" / "config" / "custom_components" / "personas_home"))

import payloads  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# --- the endpoint: TWO URL SHAPES ----------------------------------------
# A URL WITHOUT a path is a studio server base: the persona is appended. A URL
# WITH a path is already the complete endpoint and is used verbatim -- the
# shape of Bleuenn's webhooks, whose secret IS the path. Getting the shape
# wrong is not recoverable at run time: the server answers 404 and both
# services are fire-and-forget.
check("a URL without a path gets the persona",
      payloads.webhook_endpoint("http://127.0.0.1:8080", "Hestia"),
      "http://127.0.0.1:8080/webhook/Hestia")

# The config flow already strips the trailing /, but the options flow, the
# migration and a hand-written config.json do not all go through it.
check("a trailing slash does not produce a double slash",
      payloads.webhook_endpoint("http://127.0.0.1:8080/", "Hestia"),
      "http://127.0.0.1:8080/webhook/Hestia")

# A made-up endpoint of the same shape. Never a real one: the secret IS the
# path, and this repository is public.
BLEUENN = "https://bleuenn.example.ts.net/h/not-a-real-secret-0123456789abcdef"
check("a URL with a path is the endpoint, verbatim",
      payloads.webhook_endpoint(BLEUENN, "home-manager"), BLEUENN)

check("a trailing slash on a complete endpoint does not change it",
      payloads.webhook_endpoint(BLEUENN + "/", "home-manager"), BLEUENN)

# Without a scheme, urlsplit reads "127.0.0.1" as the scheme and the rest as a
# path: the URL would switch to verbatim mode and the persona would silently
# vanish. An entry without http:// must stay a base.
check("a URL without a scheme stays a base",
      payloads.webhook_endpoint("127.0.0.1:8080", "Hestia"),
      "127.0.0.1:8080/webhook/Hestia")

# The predicate the services ask to say out loud that a persona cannot apply,
# instead of dropping it in silence.
check("a complete endpoint is recognised as such",
      payloads.is_complete_endpoint(BLEUENN), True)
check("a studio base is not a complete endpoint",
      payloads.is_complete_endpoint("http://127.0.0.1:8080"), False)
check("a base with a trailing slash is not a complete endpoint",
      payloads.is_complete_endpoint("http://127.0.0.1:8080/"), False)
check("a scheme-less host:port is not a complete endpoint",
      payloads.is_complete_endpoint("127.0.0.1:8080"), False)

# --- the authentication header -------------------------------------------
# The token is per channel and optional: Bleuenn's webhooks carry their secret
# in the path, and sending them the studio's token would disclose it to a host
# that has no business with it.
check("a token produces a Bearer header",
      payloads.auth_headers("abc123"), {"Authorization": "Bearer abc123"})
check("no token, no header", payloads.auth_headers(""), {})
check("a whitespace token does not produce an empty header",
      payloads.auth_headers("   "), {})
check("a missing token produces no header",
      payloads.auth_headers(None), {})

# --- the feedback channel, as the rest_command wrote it -------------------
# The template removed from configuration.yaml, word for word:
#   '{"type": "event", "event_type": "{{ action }}", "entity_id":
#     "{{ entity_id }}", "details": {"accepted": {{ accepted | to_json }},
#     "suggestion_id": "latest"}}'
RETIRED_REST_COMMAND = {
    "type": "event",
    "event_type": "persistent_notification",
    "entity_id": "ha_notification",
    "details": {"accepted": True, "suggestion_id": "latest"},
}

check("the feedback payload is the retired rest_command's",
      payloads.feedback_payload(
          entity_id="ha_notification",
          event_type="persistent_notification",
          accepted=True,
          suggestion_id="latest"),
      RETIRED_REST_COMMAND)

# `accepted` went out through `| to_json`: a JSON boolean, never the string
# "true". Sending a string would break nothing visible on the Home Assistant
# side.
check("accepted stays a JSON boolean",
      json.dumps(payloads.feedback_payload("x", "y", False, "latest"))
      .count('"accepted": false'), 1)

check("accepted=False goes through without being mistaken for absence",
      payloads.feedback_payload("x", "y", False, "latest")["details"]["accepted"],
      False)

# --- the main channel ------------------------------------------------------
# `details` carries a STRING here, where feedback carries an object. The two
# shapes are incompatible: that is why there are two services.
check("the event payload carries the plain text",
      payloads.event_payload("Le Velux est ouvert", "ha_automation"),
      {"type": "event", "event_type": "ha_automation",
       "details": "Le Velux est ouvert"})

check("the two channels do not share the shape of details",
      isinstance(payloads.event_payload("t", "e")["details"], str)
      and isinstance(
          payloads.feedback_payload("x", "y", True, "latest")["details"], dict),
      True)

check("the conversation payload asks for a reply",
      payloads.conversation_payload("quelle heure", "abc-123", "fr"),
      {"type": "conversation", "text": "quelle heure",
       "conversation_id": "abc-123", "language": "fr"})

# --- what the log may show of an endpoint ---------------------------------
# On a complete endpoint the secret IS the path: no log line may carry it.
# A studio base carries no secret and stays readable as is.
check("a complete endpoint is logged as scheme and host only",
      payloads.endpoint_for_log(BLEUENN), "https://bleuenn.example.ts.net")
check("a complete endpoint keeps its port in the log",
      payloads.endpoint_for_log("http://10.0.0.5:8123/h/s3cr3t"),
      "http://10.0.0.5:8123")
check("a studio base is logged verbatim",
      payloads.endpoint_for_log("http://127.0.0.1:8080"),
      "http://127.0.0.1:8080")
check("a studio base is logged with its persona when one is given",
      payloads.endpoint_for_log("http://127.0.0.1:8080", "Hestia"),
      "http://127.0.0.1:8080/webhook/Hestia")
check("a complete endpoint ignores the persona in the log too",
      payloads.endpoint_for_log(BLEUENN, "home-manager"),
      "https://bleuenn.example.ts.net")
check("user info in a complete endpoint is not logged",
      payloads.endpoint_for_log("https://user:pw@example.net/h/s3cr3t"),
      "https://example.net")
SECRET_PATH = "/h/not-a-real-secret-0123456789abcdef"
check("the secret path never reaches a formatted message",
      SECRET_PATH in "Personas Studio refused %s: HTTP %s"
      % (payloads.endpoint_for_log(BLEUENN), 404), False)


class FakeResponseError(Exception):
    """Shaped like aiohttp.ClientResponseError, whose str() embeds the URL."""

    def __init__(self, url):
        super().__init__(f"404, message='Not Found', url={url!r}")
        self.status = 404
        self.message = "Not Found"
        self.request_info = object()


class FakeInvalidUrl(Exception):
    """Shaped like aiohttp.InvalidURL, whose str() IS the URL."""

    def __init__(self, url):
        super().__init__(url)
        self.url = url


check("an HTTP error is logged by status, not by its URL-bearing text",
      payloads.error_for_log(FakeResponseError(BLEUENN)),
      "FakeResponseError (HTTP 404)")
check("an error carrying the URL is logged by its class only",
      payloads.error_for_log(FakeInvalidUrl(BLEUENN)), "FakeInvalidUrl")
check("any other error keeps its text",
      payloads.error_for_log(OSError("Connection refused")),
      "OSError: Connection refused")
check("an error with no text is still named",
      payloads.error_for_log(TimeoutError()), "TimeoutError")

# Every logging site of the integration goes through these helpers: no log
# call may receive a raw endpoint, URL or exception object.
import ast  # noqa: E402

INTEGRATION = REPO / "stack" / "config" / "custom_components" / "personas_home"
RAW = {"endpoint", "url", "err", "error", "exc"}
raw_args = []
for source in sorted(INTEGRATION.glob("*.py")):
    for node in ast.walk(ast.parse(source.read_text())):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_LOGGER"):
            for arg in node.args[1:]:
                if isinstance(arg, ast.Name) and arg.id in RAW:
                    raw_args.append(f"{source.name}:{node.lineno} {arg.id}")
check("no log call receives a raw endpoint, URL or exception", raw_args, [])

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_personas_payloads: OK")
