<img src="assets/icon.png" alt="Kiosk Satellite Fleet Backup Solution for Home Assistant" width="128">

# Kiosk Satellite Fleet Backup Solution for Home Assistant

Home Assistant custom integration that backs up the full configuration of
[Kiosk Satellite](https://kiosksatellite.com) Android tablets through the kiosk's
Remote API (`GET /api/config/export`) and can restore it (`POST /api/config/import`).

## Built for Kiosk Satellite 💙

This integration only exists because of **[Kiosk Satellite](https://kiosksatellite.com)** by
**[Xavier (@jxlarrea)](https://github.com/jxlarrea)**: a free, fully local Android kiosk app
built for Home Assistant from the ground up. If you run wall tablets with Home Assistant
dashboards, give it a try:

- Native **Voice Satellite** support, so your tablet listens for your wake word even with the screen off
- A built-in **ESPHome connection** that turns every tablet into a proper Home Assistant device
  (screen, volume, screensaver, camera, notifications, announcements), plus an optional Bluetooth proxy
- **Screensavers** with Immich albums, local photos, clocks and weather, with motion, face or presence wake
- Synchronized **Music Assistant** playback, intercom between kiosks, camera views and DLNA
- A full **remote admin** in the browser, fleet management and a REST API
  (the API this integration relies on)

👉 [Website & docs](https://kiosksatellite.com) · [GitHub](https://github.com/jxlarrea/kiosk-satellite) ·
[Voice Satellite for Home Assistant](https://github.com/jxlarrea/voice-satellite-card-integration)

A big **thank you to Xavier** for building and maintaining Kiosk Satellite, and for documenting
its Remote API so well that an integration like this is possible. If Kiosk Satellite makes your
home better, consider [buying him a coffee](https://buymeacoffee.com/jxlarrea) ☕.

> Kiosk Satellite Fleet Backup Solution for Home Assistant is an independent community project. It is not affiliated with or
> endorsed by Kiosk Satellite or its author. Please report issues with this integration
> [here](https://github.com/BartRoels/ha-kiosk-satellite-backup/issues), not to the Kiosk Satellite project.

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

- Kiosk Satellite with **Remote management** on, an admin password set and **Use HTTPS** on
  (Settings → Device → Remote Administration). Plain HTTP is refused.
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
2. Confirm the URL. Leave *Verify SSL* off for the kiosk's self-signed certificate.
3. Compare the certificate fingerprint shown with **Settings → Device → TLS** on the kiosk,
   then enter the admin password. It is exchanged once for a token valid for one year and
   is **not stored**.

Home Assistant asks you to re-authenticate two weeks before the token expires, when the
kiosk rejects the token, or when the kiosk presents a certificate with a different key.

## Security

- **Certificate pinning.** The kiosk's self-signed certificate is pinned at setup. Every
  request, including the token, only goes out over a connection that presents that exact
  certificate. The kiosk renews its certificate yearly with the same key; such renewals are
  accepted automatically. A different key is refused until you re-authenticate and review
  the new fingerprint.
- **HTTPS only.** Plain `http://` URLs are refused, also when reported by the kiosk's sensor.
- **No stored password.** Only a one-year token is kept (in Home Assistant's config entry
  storage). Kiosk Satellite tokens can't be revoked, not even by changing the admin password,
  which is why they are kept short-lived.
- **Admin-only actions.** `backup` and `restore` can only be called by Home Assistant
  administrators.
- **No leaks in errors.** Kiosk responses are never copied into logs or sensor attributes.
- **Private files.** Backups are written atomically, readable only by Home Assistant
  (`0600`), and a restore can only pick files from the kiosk's own backup folder.

Found a security issue? Please open a private
[security advisory](https://github.com/BartRoels/ha-kiosk-satellite-backup/security/advisories/new)
instead of a public issue.

## Weekly schedule

```yaml
alias: Kiosk Satellite - Weekly config backup
triggers:
  - trigger: time
    at: "03:07:00"
conditions:
  - condition: time
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
