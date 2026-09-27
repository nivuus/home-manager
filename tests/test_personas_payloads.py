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

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_personas_payloads: OK")
