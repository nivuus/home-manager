# MQTT accounts: Dynamic Security

Design, evidence, live migration and rollback:
`docs/superpowers/specs/2026-11-09-mqtt-per-client-accounts-design.md`.
This page is the short version for whoever touches the broker next.

## Files

| File (under `/opt/nivuus/home-manager/`) | Owner | Written by |
|---|---|---|
| `mosquitto/mosquitto.conf` | installation data | the hook, only if it matches a `LEGACY_CONFS` entry |
| `mosquitto/mosquitto.conf.pre-dynsec` | rollback copy | the hook, once |
| `mosquitto/dynamic-security.json` | **the broker** (uid 1883, 0600) | the hook if absent, then only `mosquitto_ctrl` |
| `mosquitto/passwd` | legacy, read only | nobody since the migration |
| `mosquitto/acl_meross` | 8883 ACL | the operator |
| `.env` `MQTT_ADMIN_PASSWORD` | root, 0600 | the hook, once |

## Rules not to undo

- **Never edit `dynamic-security.json` while the broker runs**: the plugin
  rewrites it from memory after the next change and your edit is lost. Use
  `mosquitto_ctrl` (helper `mqctl` in the spec), or stop the broker first.
- **Never remove an entry from `LEGACY_CONFS`** in `hooks/mqtt_accounts.py`:
  an install that skipped releases still carries that configuration. When the
  packaged `mosquitto.conf` changes, add the previous directives there.
- **Rewrite `mosquitto.conf` in place, never by rename**: it is bind-mounted
  alone (`:ro`); a new inode stays invisible to the container.
- **Valetudo usernames are the robot identifiers**: the `valetudo` role uses
  `%u` to confine each robot to `valetudo/<identifier>/#` and its own
  discovery node.
- **8883 is not Dynamic Security**: anonymous + `acl_meross` via the
  acl-file plugin. `allow_anonymous false` stays global; only
  `listener_allow_anonymous true` on 8883 opens it.
- **`mosquitto_ctrl` exits 0 on some failures** (e.g. deleting an unknown
  client): read its output, then `getClient`.

## An edited configuration (hook WARNING)

The hook left `mosquitto.conf` alone because its directives match no known
packaged version, and created no accounts file. To migrate by hand, the
accounts file must exist BEFORE any Dynamic Security configuration is on
disk (Mosquitto 2.1 would otherwise generate an admin-only file and lock
every client out). Without touching the running broker until the last step:

1. keep your file aside (`cp mosquitto.conf ~/mosquitto.conf.mine`), then
   write the last packaged password-file configuration over it **in place**:
   `python3 -c 'import sys; sys.path.insert(0, "hooks"); import mqtt_accounts as m; print("\n".join(m.LEGACY_CONFS["password-file"]))' > /opt/nivuus/home-manager/mosquitto/mosquitto.conf`
   (run from the package checkout). It is a working configuration for the
   broker as it is today;
2. re-run the install phase: the configuration is now `legacy`, so the hook
   saves the admin secret in `.env`, creates `dynamic-security.json` from
   `mosquitto/passwd`, and only then rewrites the configuration to the
   template;
3. re-apply your own directives on top of the template (in place);
4. `docker compose up -d mosquitto`.

## Order the hook guarantees

Admin secret in `.env` (0600) -> accounts file (complete) -> Dynamic Security
configuration on disk. Every write in `mosquitto/` goes through
`hooks/safe_files.py` (`O_NOFOLLOW`, `O_EXCL`, fsync): the directory belongs
to uid 1883 and the hook writes there as root. Do not add a write there that
bypasses it. Tests: `tests/test_mqtt_migration_safety.py`.

## Image

Pinned by tag and digest in `stack/docker-compose.yml` (2.1.2-alpine). The
configuration needs >= 2.1; a 2.0 broker refuses `plugin_load`. Bump the pin
and re-run `make test-broker` together.

## Tests

`make test` covers the files; `DYNSEC_TEST_DIR=/root/work make test-broker`
proves the roles against the real image in `--network none` containers.
