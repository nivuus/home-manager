# Per-client MQTT accounts (Mosquitto Dynamic Security) — design

Date: 2026-11-09. Branch `feat/mqtt-dynsec`. Broker: Mosquitto 2.1.2,
`eclipse-mosquitto:2.1.2-alpine` pinned by digest.

## Problem

Every client of the broker — Home Assistant, zigbee2mqtt, the nivuus
`mqtt-system-agent`, two Valetudo robots, three Fully Kiosk tablets, operator
sessions — logs in as the one account `mqtt`, from the password file, with no
ACL on 1883/1884/8884. The agent runs as root and turns messages on
`system_agent/.../command|set` into root actions (firewall, Wi-Fi, VPN, VMs,
updates). Any tablet or robot, or anything that steals their shared password,
can therefore drive root on the router.

Goal: one account per device, one role per client kind, and only Home
Assistant and the agent may publish the agent's command topics. Nothing
anonymous except the Meross listener, whose behaviour does not change.

## Choice: Mosquitto's Dynamic Security plugin

Shipped in the image (`/usr/lib/mosquitto_dynamic_security.so`), maintained
upstream, administered live over `$CONTROL/dynamic-security/v1` with
`mosquitto_ctrl` — no broker restart to add a tablet, no file to hand-edit.
The alternatives were a static `acl_file` + `password_file` pair (restart or
SIGHUP for every change, and ACL `pattern` lines cannot express "this robot,
not the other one" by role) and an external auth plugin (a new dependency for
what the shipped plugin does).

Docs relied on, read on 2026-11-09:
- <https://mosquitto.org/documentation/dynamic-security/> — JSON objects,
  ACL types, priorities, `%u`/`%c` substitution ("must occupy an entire topic
  level"), default ACL access, 2.1 auto-generation.
- <https://mosquitto.org/documentation/listeners/per-listener-settings/> —
  `per_listener_settings` deprecated since 2.1, replaced by
  `plugin_load` / `plugin_use` and `listener_allow_anonymous`.
- <https://mosquitto.org/man/mosquitto-conf-5.html> — `listener_allow_anonymous`
  overrides `allow_anonymous`; `plugin_opt_*` goes to the current plugin.
- `mosquitto_ctrl dynsec help` in the 2.1.2 image (the online
  `mosquitto_ctrl_dynsec(1)` page was unreachable that day).
- Mosquitto 2.1.2 source, where the docs are silent:
  `src/conf.c` (`plugin_load`, `plugin_use`, `plugin_opt_*` attach to
  `cur_plugin`), `plugins/acl-file/plugin.c` (option `acl_file`),
  `plugins/dynamic-security/clients.c` (`encoded_password` goes through
  `mosquitto_pw_decode`), `libcommon/password_common.c` (the decoder shared
  with the password file: `$6$`, `$7$`, `$argon2id$`; PBKDF2 salt 12 or 64
  bytes, hash 64 bytes).

Every behaviour below is also proven against the real image by
`tests/broker_dynsec_container.py` (see "Evidence").

## Broker configuration

`stack/mosquitto/mosquitto.conf`, with `per_listener_settings` gone:

| Listener | Authentication / ACL | Anonymous |
|---|---|---|
| 1883, 1884, 8884 | `plugin_use dynsec` | refused (global `allow_anonymous false`) |
| 8883 TLS 1.2 | `plugin_use meross_acl` = `mosquitto_acl_file.so` + `acl_meross` | `listener_allow_anonymous true` |

The plugins are loaded once with `plugin_load <name> <path>` and applied per
listener with `plugin_use`. Dynamic Security does not see 8883; the acl-file
plugin does not see the other three. The Meross bulbs keep logging in with
their MAC (contains `:`) as username, unauthenticated, confined by the same
`pattern readwrite /appliance/#` and `/app/#` lines as before.

Note: the Meross MAC usernames would now fit in Dynamic Security (usernames
are JSON strings). Moving them is a later, separate change; this one keeps
8883 behaviour identical, as required.

## Accounts file: `mosquitto/dynamic-security.json`

Owned by uid 1883, mode 0600, next to the password file in the broker's data
directory. The hook writes it **only when absent**; afterwards the plugin owns
it and rewrites it after every `mosquitto_ctrl` change.

Default ACL access: publish **deny**, subscribe **deny**, receive allow,
unsubscribe allow (receive only ever concerns subscriptions an ACL allowed).

### Roles — one per client kind

| Role | Publish | Subscribe |
|---|---|---|
| `home-assistant` | `#` | `#` |
| `zigbee2mqtt` | `zigbee2mqtt/#`, `homeassistant/#` except `homeassistant/+/nivuus/#` and `homeassistant/status` (deny, priority 10) | `zigbee2mqtt/#`, `homeassistant/#` |
| `system-agent` | `system_agent/#`, `homeassistant/+/nivuus/#` | same + `homeassistant/status` |
| `valetudo` | `valetudo/%u/#`, `homeassistant/+/%u/#` | same + `homeassistant/status` |
| `fully-kiosk` | `fully/#` | nothing |
| `operator` | `#` | `#`, `$SYS/#` |
| `legacy-full-access` | `#` | `#` |
| `dynsec-admin` | `$CONTROL/dynamic-security/#` | same |

Why each one is shaped this way:

- **zigbee2mqtt** names its discovery nodes after IEEE addresses and group ids
  (`homeassistant/<component>/0x…/…`), which no pattern can enumerate, so it
  gets `homeassistant/#` minus the agent's discovery node and HA's birth topic.
  It also subscribes to `homeassistant/#` to clear stale discovery.
- **system-agent**: every topic of the agent goes through
  `BaseFeature.prefixTopic()` (base topic `system_agent`), its LWT is
  `system_agent/<id>/status`, and its discovery node is its identifier,
  `nivuus`. Checked in `nivuus/mqtt` `src/core/BaseFeature.ts` and
  `src/mqtt/MqttClient.ts`. The `systemd-failure-notify` CLI shares its
  credentials (`/etc/mqtt-system-agent.env`), hence its role.
- **valetudo: one role, per-device isolation through `%u`.** Valetudo
  publishes under `valetudo/<identifier>/` and announces discovery with the
  identifier as node id, so a robot whose MQTT username **is** its identifier
  (`GenerousHideousBison`, `WanImpartialQuail`) can reach only its own tree.
  Proven: one robot cannot publish or subscribe the other's tree, nor
  subscribe `valetudo/+/#`. If Homie autodiscovery is enabled on a robot it
  publishes `homie/<identifier>/…` too, which this role denies: turn Homie off
  in Valetudo (HA uses the HA discovery) or add `homie/%u/#` to the role.
- **fully-kiosk: one role per kind, no per-device isolation.** The tablets
  publish on `fully/<room>/event/…` and `fully/deviceInfo/<deviceId>`; the
  second carries Fully's device id, not anything the broker knows, so `%u`
  cannot pin it without reconfiguring every tablet's topics. All three tablets
  are of the same trust level and their worst case is a spoofed tablet event;
  they cannot reach the agent, z2m, Valetudo or HA's discovery. Each tablet
  still gets its own client (and can be disabled alone).
- **operator** is for interactive `mosquitto_sub/pub`. Only root can
  `docker exec` into the broker, and root already owns the host, so this role
  grants nothing new; it exists so debugging never needs a device's password.
- **legacy-full-access** carries the accounts of the password file with the
  unrestricted access they had (no ACL existed on those listeners). It is
  deleted when the last legacy account is.
- **dynsec-admin** manages accounts and nothing else: the admin cannot publish
  application topics (proven).

Residual risk, accepted: a role allowed to publish discovery (`zigbee2mqtt`,
`valetudo` on its node) could announce an HA entity whose `command_topic`
points at `system_agent/…`; HA would then publish there when a person uses
that entity. The broker cannot inspect discovery payloads. Mitigation is HA
side (review new MQTT entities), out of scope.

### Clients the hook creates

- `nivuus-admin`, role `dynsec-admin`. Its password is drawn by the hook
  (`secrets.token_urlsafe(32)`), stored as `MQTT_ADMIN_PASSWORD` in
  `/opt/nivuus/home-manager/.env` (root, 0600, outside the broker's data
  directory so the container never sees it), and hashed into the JSON as
  `$7$210000$<64-byte salt>$<64-byte hash>` (PBKDF2-SHA512, OWASP's iteration
  count; measured: an admin command takes ~0.25 s). If `.env` already has
  `MQTT_ADMIN_PASSWORD` when the JSON must be (re)created, that value is
  reused. Named `nivuus-admin` rather than `admin` to avoid colliding with a
  carried-over account; a collision stops the phase.
- Every `user:hash` line of `mosquitto/passwd`, verbatim, role
  `legacy-full-access`. **No plaintext is needed**: the password file and the
  plugin decode hashes with the same function, so the hash string is copied
  into `encoded_password`. Proven with the live `mqtt` hash
  (`$7$101$…`, Mosquitto 2.0 format, 12-byte salt) on a copy of the live data
  directory: accepted on 1883, 1884 and 8884 after migration. A line that is
  not a hash (plaintext file) stops the phase before any write.
- On a fresh install (no password file), the wizard's `mqtt_password`, if
  answered, becomes the `mqtt` client — the account the bootstrap
  `zigbee2mqtt/configuration.yaml` logs in with. It never changes an existing
  account.

## Migration of an existing install (hooks/install.py + hooks/mqtt_accounts.py)

`mosquitto/mosquitto.conf` stays installation data (PRESERVED).

**Invariant: a Dynamic Security configuration is never on disk without an
accounts file that carries every password-file account and the admin, and
that accounts file is never on disk without its admin secret saved in
`.env`.** Mosquitto 2.1 generates an admin-only accounts file when the one it
is configured with is missing; breaking the invariant, even for the length of
a crash, locks the whole house out. The hook:

1. **Plans before the first write** (rule 2 of `install.py`). Classifies the
   installed configuration by its directives, comments and blank lines
   ignored — `current` (the new template), `legacy` (a version this package
   laid down, listed in `LEGACY_CONFS`), `unknown`, or `absent`. Reads and
   validates the password file. **If an accounts file already exists and the
   hook is about to put a Dynamic Security configuration on disk (`legacy` or
   `absent`), it must name the admin and every password-file account**;
   otherwise the phase stops and names the missing accounts. (Not checked
   once `current`: the procedure's last step deletes `mqtt` on purpose.)
   Refuses any symbolic link or non-regular file at the broker directory,
   the configuration, the password file, the accounts file or the backup.
   Any refusal exits 1 with nothing written.
2. If a new admin secret was drawn: writes it to `.env` first (temp file
   created `O_EXCL|O_NOFOLLOW` with mode 0600, fsync, rename, directory
   fsync). A re-run after a later failure reuses it.
3. If the configuration is `legacy`, `current` or `absent` and no accounts
   file exists: writes it the same way, `fchown` to 1883 when root.
4. Lays the stack down. `copytree` now **skips** every PRESERVED file that
   exists instead of overwriting it and writing it back afterwards (the old
   save/restore left the packaged Dynamic Security configuration on disk
   between the two). On a fresh install this is where the template appears —
   after step 3.
5. If `legacy`: creates `mosquitto/mosquitto.conf.pre-dynsec` (`O_EXCL`,
   fsync; an existing regular backup is kept), then rewrites the
   configuration **in place** (`O_NOFOLLOW`, truncate, fsync). In place
   matters: the file is bind-mounted alone into the container, and a rename
   would leave the mount on the old inode.
6. If `unknown`: touches neither file, prints a WARNING on stderr and in the
   progress events, and succeeds. Failing would block every future update of
   the package for a hand-edited broker file.

**Why the symlink care.** The broker's data directory belongs to uid 1883, the
uid the broker runs as, and the hook writes there as root. A compromised
broker could plant `passwd -> /etc/shadow` (its `$6$` lines would be "carried
over" into a file uid 1883 reads) or `mosquitto.conf.pre-dynsec -> <any file>`
(root would overwrite it). All reads and writes there go through
`hooks/safe_files.py`: `O_NOFOLLOW` everywhere, `O_EXCL` creation, a stale
temporary unlinked (the link, never its target) before an exclusive create.

The live configuration differs from the 2026-08-28 template only in comments,
so it is recognised as `legacy` (proven on a copy).

The hook does not restart the broker (the activate phase only starts services
without a container), so **nothing changes on the live broker until the
operator recreates it**. Re-running the hook is a no-op: the JSON exists, the
configuration is `current`, the `.env` key is present — checked by the
engine's idempotence harness, as root and as an unprivileged user.

**Image.** `stack/docker-compose.yml` pins
`eclipse-mosquitto:2.1.2-alpine@sha256:75d6662d…` (Docker Hub has no plain
`2.1.2` tag; `2.1-alpine`, `2` and `latest` point at the same digest on
2026-11-09). The configuration needs >= 2.1: a 2.0.22 broker refuses it at
start (`Error: Unknown configuration variable "plugin_load"`, proven). The
hook cannot see which image the running container uses without querying the
live container, which it must not do; `tests/test_mqtt_accounts.py` instead
fails if the pin is removed or drops below 2.1, and step 0 below checks the
running broker.

## Live migration — operator steps

```bash
# 0. The package update (nivuus update home-manager) ran the hook. Check:
cd /opt/nivuus/home-manager
grep -c '^plugin_use' mosquitto/mosquitto.conf        # 4
ls -l mosquitto/dynamic-security.json mosquitto/mosquitto.conf.pre-dynsec
grep -c '^MQTT_ADMIN_PASSWORD=' .env                 # 1
grep -c '"username": "mqtt"' mosquitto/dynamic-security.json   # 1

# 1. Apply: recreate the broker on the pinned 2.1 image (a plain restart
#    would keep the old container's image). Clients reconnect within seconds,
#    still as mqtt.
docker compose up -d mosquitto
docker exec mosquitto mosquitto -h | head -1          # mosquitto version 2.1.x
grep -aE 'dynamic-security|acl-file|Error' mosquitto/mosquitto.log | tail
```

From here every step is live and reversible, one client at a time. Define the
helper once per shell; it keeps the admin password and device passwords out
of every command line (`docker exec -e VAR` passes the value through the
environment, the container shell expands it into a here-document read with
`-o /dev/fd/3`, and `mosquitto_ctrl` reads a new client password from stdin):

```bash
export MQTT_ADMIN_PASSWORD="$(sed -n 's/^MQTT_ADMIN_PASSWORD=//p' /opt/nivuus/home-manager/.env)"
mqctl() {
  docker exec -i -e MQTT_ADMIN_PASSWORD "${MQTT_BROKER:-mosquitto}" sh -c 'exec 3<<EOF
-u nivuus-admin
-P $MQTT_ADMIN_PASSWORD
EOF
exec mosquitto_ctrl -o /dev/fd/3 dynsec "$@"' mqctl "$@"
}
# new client:   pw="$(openssl rand -base64 24)"; printf '%s\n%s\n' "$pw" "$pw" | mqctl createClient <user>
# its role:     mqctl addClientRole <user> <role>
# check:        mqctl getClient <user>      (mosquitto_ctrl exits 0 even on some errors: read the output)
```

Per client, create the account, switch the client to it, watch it reconnect:

| Client | Username | Role | Where its credentials live |
|---|---|---|---|
| Home Assistant | `homeassistant` | `home-assistant` | Settings → Devices → MQTT → Reconfigure |
| zigbee2mqtt | `zigbee2mqtt` | `zigbee2mqtt` | `zigbee2mqtt/configuration.yaml` `mqtt.user/password` (or its frontend), then `docker restart zigbee2mqtt` |
| system agent + notifiers | `mqtt-system-agent` | `system-agent` | `/etc/mqtt-system-agent.env` `MQTT_USERNAME/MQTT_PASSWORD`, then restart `mqtt-system-agent` |
| robot 1 | `GenerousHideousBison` | `valetudo` | Valetudo UI → Connectivity → MQTT (username **must** equal the identifier) |
| robot 2 | `WanImpartialQuail` | `valetudo` | idem |
| tablets | `fully-salon`, `fully-cuisine`, `fully-bureau` | `fully-kiosk` | Fully → Settings → MQTT |
| operator | `operator` | `operator` | your shell |

Example, one tablet:

```bash
pw="$(openssl rand -base64 24)"; echo "$pw"     # paste into Fully
printf '%s\n%s\n' "$pw" "$pw" | mqctl createClient fully-salon
mqctl addClientRole fully-salon fully-kiosk
grep -a "fully-salon" /opt/nivuus/home-manager/mosquitto/mosquitto.log | tail -3
```

Before the last step, prove `mqtt` is no longer used — no `u'mqtt'` connection
in the log since the last client moved:

```bash
grep -a "New client connected" /opt/nivuus/home-manager/mosquitto/mosquitto.log \
  | grep "u'mqtt'" | tail -5
```

Final step:

```bash
mqctl deleteClient mqtt
mqctl deleteRole legacy-full-access          # once no client carries it
```

`mosquitto/passwd` is then unused; keep it until rollback is no longer wanted.

## Rollback

Before `mqtt` is deleted, rollback is one file and one restart — the
password file was never modified:

```bash
cd /opt/nivuus/home-manager
cat mosquitto/mosquitto.conf.pre-dynsec > mosquitto/mosquitto.conf   # in place
docker restart mosquitto
```

Every client still configured as `mqtt` works again. Clients already moved to
a per-device account do not (they are unknown to the password file): move
them back to `mqtt`, or add them with `mosquitto_passwd` first. The JSON stays
on disk, harmless and ready for the next attempt.

A later package update migrates the configuration again (it is recognised as
`legacy`), but only if that accounts file still names the admin and **every**
account of the password file. An account added to `passwd` while rolled back
(or `mqtt` deleted from the JSON before the rollback) makes the hook stop and
name it. Two ways out (`mosquitto_ctrl -f <file>` only edits passwords
offline: `createClient` and `addClientRole` with `-f` exit 0 and change
nothing — tested):

- **Start over from the password file**: move the JSON aside
  (`mv mosquitto/dynamic-security.json mosquitto/dynamic-security.json.old`)
  and re-run the install phase; it is recreated with every `passwd` account
  and the `.env` admin secret. Per-device clients of the old file must be
  created again.
- **Keep the per-device clients**: edit a COPY with a throwaway broker
  (never the live data directory: a second broker would share
  `mosquitto.db`), then put it back:

```bash
IMG=eclipse-mosquitto:2.1.2-alpine@sha256:75d6662dee52c947dc9dd9c29f1d8d7ffef78ddaf38001caa09755681e8bbc98
W=$(mktemp -d /root/dynsec-edit.XXXX)
cp /opt/nivuus/home-manager/mosquitto/dynamic-security.json "$W/"
printf 'plugin_load dynsec /usr/lib/mosquitto_dynamic_security.so\nplugin_opt_config_file /mosquitto/data/dynamic-security.json\nlistener 1883\nplugin_use dynsec\n' > "$W/mosquitto.conf"
docker run --rm -d --network none --name dynsec-edit -v "$W:/mosquitto/data" \
  -v "$W/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" "$IMG"
# mqctl as above, aimed at the copy: export MQTT_BROKER=dynsec-edit (unset it after)
#   printf '%s\n%s\n' "$pw" "$pw" | mqctl createClient <user>; mqctl addClientRole <user> <role>
docker rm -f dynsec-edit
install -o 1883 -g 1883 -m 0600 "$W/dynamic-security.json" /opt/nivuus/home-manager/mosquitto/dynamic-security.json
```

The hook refuses rather than merging the missing accounts itself: merging
would silently hand them `legacy-full-access`, while the point of the
migration is that each account's access is a decision. To stay on the
password file, edit any directive (the configuration becomes `unknown` and is
left alone).

## Evidence (tests)

- `tests/test_mqtt_accounts.py` (in `make test`): classification, password
  file parsing, the `$7$` encoder verified like Mosquitto verifies, the role
  table (only HA, agent, operator, legacy may publish
  `system_agent/nivuus/reboot/set`), every install path (fresh, legacy,
  edited, existing JSON complete or missing an account or the admin,
  reused secret, refusals before any write), idempotence, the image pin.
- `tests/test_mqtt_migration_safety.py` (in `make test`): the order under
  failure (an accounts write that fails leaves the legacy configuration, no
  template on a fresh install, and the admin secret already in a 0600 `.env`,
  which the re-run reuses) and planted symbolic links (configuration,
  password file, accounts file, backup, broker directory: refused, target
  untouched; stale temporary: replaced, target untouched). A mutation run
  (copy before accounts, `O_NOFOLLOW`/`O_EXCL` removed) turns 6 checks red —
  including the password file pointing at a shadow-style file being read.
- `tests/broker_dynsec_container.py` (`make test-broker`, root + docker):
  throwaway `--network none` containers of the compose's image, clients via
  `docker exec`. Legacy broker → hook → migrated broker; 67 checks: legacy
  `mqtt` on 1883/1884/8884, anonymous and wrong password refused there, each
  role allowed and denied where the table says, `%u` isolation between
  robots, the Meross listener (MAC username and no username) limited to
  `/appliance/#` and `/app/#` with `#` leaking nothing, second hook pass
  changes nothing, per-device client survives restart, `mqtt` refused once
  deleted, rollback works. A mutation run (widening `fully-kiosk` and the
  Meross ACL) turns 10 checks red, so the "denied" checks can fail.

## Not proven / open

- Not done on the live broker (deliberately): the restart and the per-device
  moves are the operator's.
- `%c` (client id) is not used: no client of this house has a stable,
  meaningful client id (the agent's is random-suffixed).
- The 8883 log warns "ACL pattern '/appliance/#' does not contain '%c' or
  '%u'" — same lines as before, harmless.
- A fresh install still lacks `acl_meross` and the TLS certificates, which the
  configuration references (pre-existing; unchanged by this work).
- The pinned digest is a newer build of 2.1.2 than the one running live
  (local `latest`, created 2026-06-22); the container proof ran on the pinned
  one, the live-hash proof on the live one.
- A crash between `rename` and the directory `fsync` is covered by the
  filesystem's own ordering, not by a test.
