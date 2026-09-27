"""Constants for the Personas Studio integration."""

DOMAIN = "personas_home"
NAME = "Personas Studio"

# THREE CHANNELS, THREE DESTINATIONS. They were one until 2026-09-06, when the
# conversation entity and the life events stopped sharing a correspondent: the
# entity still asks the personas-studio persona and WAITS for its answer, while
# the events go to Bleuenn's webhook, which answers 202 with no body. Pointing
# the entity at the latter would leave the four conversation.process callers —
# scripts.yaml and three automations, one of which parses the returned JSON —
# with empty replies and no error anywhere.
CONF_URL = "url"
CONF_PERSONA = "persona"
CONF_TOKEN = "token"

CONF_EVENT_URL = "event_url"
CONF_EVENT_PERSONA = "event_persona"
CONF_EVENT_TOKEN = "event_token"

CONF_FEEDBACK_URL = "feedback_url"
CONF_FEEDBACK_PERSONA = "feedback_persona"
CONF_FEEDBACK_TOKEN = "feedback_token"

# (name, url key, persona key, token key). The form, the validation and the
# migration all walk this tuple, so adding a channel is one line here.
CHANNELS = (
    ("conversation", CONF_URL, CONF_PERSONA, CONF_TOKEN),
    ("event", CONF_EVENT_URL, CONF_EVENT_PERSONA, CONF_EVENT_TOKEN),
    ("feedback", CONF_FEEDBACK_URL, CONF_FEEDBACK_PERSONA, CONF_FEEDBACK_TOKEN),
)

DEFAULT_PERSONA = "home-manager"
DEFAULT_TIMEOUT = 120

# 1 -> 2 added the feedback channel; 2 -> 3 split the event channel off the
# conversation one and gave every channel its own token. An entry from an
# earlier version has none of those keys, and reading one raises KeyError at
# setup — the integration would simply not load. See async_migrate_entry.
CONFIG_VERSION = 3
