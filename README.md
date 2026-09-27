<img src="assets/icon.png" alt="" width="128" align="left">

# Kiosk Satellite Backup

Home Assistant custom integration that backs up the full configuration of
[Kiosk Satellite](https://kiosksatellite.com) Android tablets through the kiosk's
Remote API (`GET /api/config/export`) and can restore it (`POST /api/config/import`).

## What you get

Per kiosk (one config entry each), added to the kiosk's existing ESPHome device:

| Entity | Purpose |
|---|---|
| `button.<kiosk>_backup_configuration` | Take a backup now |
| `sensor.<kiosk>_last_backup` | Timestamp of the last good backup; attributes: file, size, backups stored, retention |
| `sensor.<kiosk>_backup_status` | `ok` / `failed` / `never`; attributes: last attempt, error |

Actions:

- `kiosk_satellite_backup.backup` — back up one, several or (empty) all kiosks. Fails the
  run if any kiosk fails, or returns per-kiosk results when a response is requested.
- `kiosk_satellite_backup.restore` — push a stored backup (newest by default) back to a kiosk.
  Keep *Adopt identity* on to restore the same tablet; turn it and *Import page data* off to
  clone onto a different tablet.

Backups are stored in `<config>/kiosk_satellite_backups/<kiosk>/ks-backup_<kiosk>_<YYYYmmdd_HHMMSS>.json`
(folder `0700`, files `0600`), newest *N* kept (default 8, change under **Configure**).
Because they live in `/config`, regular Home Assistant backups include them.

> The export contains **secrets** (the kiosk's Home Assistant token, passwords).
> Treat the folder and your HA backups accordingly.

## Requirements

- Kiosk Satellite with **Remote management** on and an admin password set
  (Settings → Device → Remote Administration).
- Home Assistant 2025.2 or newer.

## Install

**HACS (custom repository):** HACS → ⋮ → Custom repositories → add this repo's URL,
category *Integration* → download → restart Home Assistant.

**Manual:** copy `custom_components/kiosk_satellite_backup` to
`/config/custom_components/kiosk_satellite_backup` → restart Home Assistant.

## Set up

Settings → Devices & services → Add integration → **Kiosk Satellite Backup**, once per tablet:

1. Pick the tablet's ESPHome device. The admin URL is pre-filled from its *Remote admin*
   sensor, and later backups follow that sensor if the tablet's IP changes.
   (Leave empty to add a kiosk manually by name and URL.)
2. Enter the admin password. It is exchanged once for a long-lived token
   (`ttl_days: 3650`) and **not stored**. Leave *Verify SSL* off for the kiosk's
   self-signed certificate.

If the kiosk ever rejects the token, Home Assistant raises a re-authentication prompt.

## Weekly schedule

```yaml
alias: Kiosk Satellite - Weekly config backup
triggers:
  - trigger: time
    at: "03:07:00"
    weekday: [sun]
actions:
  - action: kiosk_satellite_backup.backup
    response_variable: result
  - if:
      - condition: template
        value_template: "{{ result.results | selectattr('status', 'eq', 'failed') | list | count > 0 }}"
    then:
      - action: notify.mobile_app_<your_phone>
        data:
          title: Kiosk backup failed
          message: >-
            {{ result.results | selectattr('status', 'eq', 'failed')
               | map(attribute='name') | join(', ') }}
mode: single
```

## Development

```bash
pip install pytest-homeassistant-custom-component
pytest
```
