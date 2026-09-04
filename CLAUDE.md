# Project rules — lab-reports

## Commands

- **"push"** means **commit → push → deploy**. When Nestor says "push":
  1. Commit staged/relevant changes (plain message, no attribution trailer).
  2. Push to `origin` (GitHub `nalepy/lab-reports`). No SSH key in shell. Do NOT inline a PAT in the URL — the auto-mode classifier blocks it as secret-exfil. Push token-free via the `gh` credential helper (gh is authed as `nalepy`, `repo` scope):
     `git -c credential.helper='!gh auth git-credential' push https://github.com/nalepy/lab-reports.git master`
  3. Deploy to **VM2** (São Paulo, `159.112.180.199`): SCP changed app files to `~/lab-reports/`, compile-check, `sudo systemctl restart lab-reports.service`, confirm `active`.

## Deploy target

- Prod runs on **VM2** via `lab-reports.service` (uvicorn, `app.server:app`, port 8083, host 0.0.0.0).
- Key: `~/Workspace/Oracle/A1-VM2-ubuntu-saopaulo-159.112.180.199.key`
- App path on VM: `~/lab-reports/`, venv at `~/lab-reports/venv/`.
- DB: `~/lab-reports/data/labs.db` (SQLite).
