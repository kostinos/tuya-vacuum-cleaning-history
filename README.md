# Tuya Vacuum Cleaning History

Home Assistant custom integration for viewing archived Tuya robot-vacuum
cleanings as an authenticated dashboard card.

> [!IMPORTANT]
> Compatibility has been tested **only with Tuvio TR06HLCB**. Other Tuya
> vacuums can use different archive layouts, route encodings, DP values, or
> cloud APIs and are not currently verified.

## Features

- archived cleaning list, duration, and area;
- room map with the robot's saved route overlaid in blue;
- cleaning scope: apartment, rooms, zone, or spot;
- historical dry/wet cleaning mode, suction power, and water level;
- responsive Lovelace card loaded by the integration;
- maps remain behind Home Assistant authentication and are never written to
  public `/local` storage;
- read-only: the integration sends no commands to the vacuum.

The scope comes from Tuya Cloud report logs near the archive timestamp. Mode,
suction, and water level come from Home Assistant Recorder at the start of that
specific cleaning. Current settings are never substituted for missing history.

## Requirements

- Home Assistant 2026.9 or newer;
- your own Tuya Cloud project Access ID and Access Secret;
- the project's data center and the vacuum's Tuya device ID;
- project access to the vacuum cleaning archive APIs;
- Recorder history for optional mode, suction, and water entities.

No LocalTuya or Tuya Local authorization is required. Credentials are stored in
this integration's Home Assistant config entry. They are never sent to the
browser, included in map URLs exposed to the browser, or stored in this repository.

## Installation with HACS

1. Add `https://github.com/kostinos/tuya-vacuum-cleaning-history` as an Integration
   custom repository in HACS, install it, and restart Home Assistant.
2. In Settings → Devices & services → Add integration, select
   **Tuya Vacuum Cleaning History**.
3. Enter your Tuya project's Access ID, Access Secret, data center, and vacuum
   device ID. Setup checks access to the cleaning archive before saving.
4. Optionally select Recorder sources for cleaning mode, suction, and water.
5. Add a Manual dashboard card:

   ```yaml
   type: custom:tuvio-history-card
   ```

Data center codes: `eu` (Central Europe), `we` (Western Europe), `us` (Western
America), `ue` (Eastern America), `cn` (China), `in` (India), `sg` (Singapore).
Choose the data center of the Tuya project, not your current location.

Use the integration's **Reconfigure** menu to change its credentials or device.

## Upgrading from v1.1.0 / legacy YAML

Existing UI entries migrate automatically: the previous cloud keys and vacuum ID
are copied into this integration once, and the external entry references are
removed. Deleting those other integrations afterwards does not affect history.
If an old cloud entry has already been deleted, Home Assistant prompts for new
credentials instead of silently failing setup.

Legacy `cloud_entry_id` / `vacuum_entry_id` YAML is supported for a one-time
import. After the history entry appears under Devices & services, remove the
`tuvio_history` YAML block and restart again. Missing old cloud credentials can
be entered through the resulting reauthentication notification.

For a new YAML import you can also provide `client_id`, `client_secret`,
`device_id`, and `region` directly (use `!secret` for credentials). YAML is a
one-time import; subsequent changes should use Reconfigure.

If the card was installed manually before v1.1.0, remove the old
`/local/tuvio-history-card.js` dashboard resource. Its bundled copy loads automatically.

## Data retention

Tuya Cloud log retention determines how far back the cleaning scope can be
reconstructed. Home Assistant Recorder retention determines how far back
mode, suction, and water settings are available. Older maps still remain
usable, but missing historical metadata is left blank.

## Access and request limits

Authenticated Home Assistant users can view the cleaning archive and maps.
Recorder-derived mode, suction, and water fields are shown only when that user
has read permission for the configured source entity. Permission filtering also
applies to cached responses.

Identical in-flight page/map requests share one load. Forced refresh reuses a
page fetched less than 30 seconds ago; ordinary reads reuse it for five minutes.
The backend admits at most two pending commands per user and eight overall,
with rolling limits of 20 commands per user and 60 overall per minute. At most
four different loads can run, including work whose callers have disconnected.
Excess requests return a retry-later error. Unloading cancels remaining loads.

Map downloads use a separate credential-free client, HTTPS on port 443, and
public IP addresses only. DNS results are checked by the connection resolver;
private/local addresses, automatic redirects, cookies, and environment proxies
are not allowed. The existing 25-second download timeout and 8 MB size limit
remain. Tuya does not document a complete storage-host allowlist; public storage
domains are accepted subject to these checks. Storage links requiring redirects
or private DNS destinations are intentionally rejected.

## Support

When reporting a problem, include the vacuum model, Home Assistant version,
integration logs, and whether the archived map itself loads. Do not publish
Tuya client secrets or signed map download URLs.

- [Issues](https://github.com/kostinos/tuya-vacuum-cleaning-history/issues)
- [Tuya vacuum map API documentation](https://developer.tuya.com/en/docs/app-development/sweeper-oss?id=Kf6jwv0u0fl1d)

## License

MIT
