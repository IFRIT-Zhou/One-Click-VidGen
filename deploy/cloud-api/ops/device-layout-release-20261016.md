# Device list layout release — 2026-10-16

Published at 10:07 UTC (18:07 Asia/Shanghai). The device list now uses compact,
wrapping cards rather than the fixed-width eight-column table. Secondary
configuration fields remain on the device detail page. The device-page mobile
navigation also wraps without a horizontal scroll container.

## Connection and files

From this Windows workstation, the working production SSH route is:

```powershell
wsl -d RayWorker -u root -- ssh root@10.250.0.1
```

The Windows default WSL distribution is Docker Desktop and does not contain
the required SSH client. Select RayWorker explicitly.

Site root: `/var/www/oneclickvidgen`. Only these four static files were updated:

- `assets/admin-subpage.js`
- `assets/admin-pages.css`
- `admin/devices.html`
- `admin/device.html`

Both device HTML files now reference cache version `20261016.devicescompact2`.
No backend or GPU service restart was required. Container-control availability
still depends on the separately deployed backend and node control roles; this
static release does not install those components.

## Verification and recovery

- 9 frontend regression tests passed, plus JavaScript syntax validation.
- Browser layout checks at 1366px and 390px used representative device data;
  all visible elements fit their horizontal bounds, including mobile navigation.
- Public HTTPS GETs of all four files on both `oneclickvidgen.com` and
  `www.oneclickvidgen.com` returned HTTP 200 with SHA-256 hashes identical to
  the release manifest. The browser layout check used a local fixture, not an
  authenticated production browser session.
- Pre-release versions and the hash manifest are saved at
  `/opt/ops-backups/device-layout-20261016T100710Z`.
- Staged release: `/opt/ops-staging/device-layout-20261016T100710Z`.

The installer verified the original server hashes before replacing files,
backed up all four files first, and used atomic file replacements. Restore the
same four relative paths from the backup together if rollback is needed.
