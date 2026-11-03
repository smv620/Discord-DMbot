# Server: the data disk and Docker/containerd start-up order

Status: in place on the test server since 2026-11-03. The owner added the disk and ran the partition and mount steps; dev1 did the rest. This page is for whoever runs the server next, so it has no identifiers, keys or names.

## What is set up

| Thing | Where | Set by |
|---|---|---|
| Data disk (100 GB, ext4, label `dmbot-data`) | mounted at `/data` | `/etc/fstab` |
| Docker data (containers, volumes, build cache) | `/data/docker` | `/etc/docker/daemon.json` (`data-root`) |
| containerd data (image layers) | `/data/containerd` | `/etc/containerd/config.toml` (`root`) |
| Database files (Postgres volume) | under `/data/docker/volumes/` | follows Docker's data root |
| Start-up rule for Docker | `/etc/systemd/system/docker.service.d/override.conf` | `RequiresMountsFor=/data` |
| Start-up rule for containerd | `/etc/systemd/system/containerd.service.d/override.conf` | `RequiresMountsFor=/data` |

The fstab line is `LABEL=dmbot-data /data ext4 defaults,nofail 0 2`. It mounts by label, which survives a reboot where the device name changes. `nofail` lets the server boot if the disk is missing; the two start-up rules then keep Docker and containerd from starting, so the bot and database never come up on an empty folder.

The config backup from the containerd change is kept next to the config as `config.toml.bak-<date>`.

## Why the start-up rules were needed

- `/data` was mounted by hand after the disk was added, and systemd was still using the old fstab until `systemctl daemon-reload`.
- Docker had no rule to wait for `/data`, so after a reboot where the disk was slow, Docker could start on an empty `/data/docker` folder.

## Steps, in order

Run these as a user with sudo. Only stop the stack when no session is running at the table.

1. **Partition and format** (once, erases the disk):
   ```bash
   sudo parted -s /dev/sdb mklabel gpt mkpart dmbot-data ext4 1MiB 100%
   sudo mkfs.ext4 -L dmbot-data /dev/sdb1
   ```
2. **Mount it and keep it mounted:**
   ```bash
   sudo mkdir -p /data
   sudo mount /dev/sdb1 /data
   echo 'LABEL=dmbot-data /data ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
   sudo systemctl daemon-reload
   ```
3. **Move Docker's data** (stop the stack first; take a database backup first):
   ```bash
   cd ~/Discord-DMbot && docker compose stop core web-api ears cloudflared postgres
   sudo systemctl stop docker docker.socket
   sudo rsync -aHAX /var/lib/docker/ /data/docker/
   echo '{"data-root": "/data/docker"}' | sudo tee /etc/docker/daemon.json
   ```
4. **Move containerd's data:**
   ```bash
   sudo systemctl stop containerd
   sudo rsync -aHAX /var/lib/containerd/ /data/containerd/
   sudo cp /etc/containerd/config.toml /etc/containerd/config.toml.bak-$(date -u +%Y%m%d)
   sudo sed -i 's|^#root = "/var/lib/containerd"|root = "/data/containerd"|' /etc/containerd/config.toml
   ```
5. **Add the start-up rules** (no editor needed):
   ```bash
   sudo mkdir -p /etc/systemd/system/docker.service.d /etc/systemd/system/containerd.service.d
   printf '[Unit]\nRequiresMountsFor=/data\n' | sudo tee /etc/systemd/system/docker.service.d/override.conf
   printf '[Unit]\nRequiresMountsFor=/data\n' | sudo tee /etc/systemd/system/containerd.service.d/override.conf
   sudo systemctl daemon-reload
   ```
6. **Start everything and check the bot** (see "Checks" below), then bring the stack up:
   ```bash
   sudo systemctl start containerd && sudo systemctl start docker
   cd ~/Discord-DMbot && docker compose up -d
   ```
7. **Remove the old copies** only after the checks pass and the owner agrees. Use the folder names, not a wildcard. A wildcard such as `/var/lib/containerd/*` is expanded by the calling user's shell before `sudo` runs; if that user can't read the folder, the pattern matches nothing and `rm -rf` silently does nothing.
   ```bash
   sudo rm -rf /var/lib/containerd /var/lib/docker
   ```

## Checks

Run after step 6, and again after the next reboot.

```bash
findmnt /data                                           # source /dev/sdb1, ext4
docker info -f '{{.DockerRootDir}}'                     # /data/docker
grep -n '^root' /etc/containerd/config.toml             # root = "/data/containerd"
systemctl show docker.service containerd.service -p RequiresMountsFor   # /data for both
df -h /                                                 # root disk back to the size it was before the move
docker ps --format '{{.Names}} {{.Status}}'             # all containers up; web-api healthy
docker logs --since 10m discord-dmbot-core-1 2>&1 | grep -c "Connected to"   # at least 1
docker logs --since 30m discord-dmbot-core-1 2>&1 | grep -c ERROR              # 0
```

Counts from the move, for the record: the containerd copy had 422,692 files and 41 GB on both sides; `rsync` exited 0. After the old copies were removed, the root disk went from 77% used to 12%.

## Rollback

Until the old copies are removed, put the data roots back to `/var/lib/docker` and `/var/lib/containerd`, remove `daemon.json`'s `data-root` line, restore `config.toml` from the backup, and remove the two drop-ins. After step 7, rollback means copying the data back from `/data` first; do not run step 7 until the next reboot has been checked.

## Notes for next time

- A missing disk stops Docker and containerd at boot, by design. Check `findmnt /data` first.
- The bot's own deploy steps are unchanged; see the deploy notes in `docs/testing-history.log` and `docs/testing-status.log`.
