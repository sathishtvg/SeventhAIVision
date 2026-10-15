# Moving, serving and deploying Seventh AI Vision

Written 15 October 2026, for the stack as it is on `main` at migration `0162`.

Three parts:

- **Part A** - copy the whole application, images, data and settings, from one
  laptop to another (for example to a new GPU laptop).
- **Part B** - run a laptop as the server for an office or a site, so browsers,
  the desktop app, phones and trackers on the same network use it.
- **Part C** - which platform to deploy on, from demos to paying customers.
- **Part D** - the AWS demo server, step by step, with the scripts in `scripts/aws/`.

It replaces sections 1, 4 and 5 of `docs/GPU-LAPTOP-SETUP.md`, which was written
at migration `0086` and moved only the database. That guide's traps (section 6)
still apply.

Every command is for **Git Bash** on Windows, run from the repository folder
unless it says otherwise. Paths given to Docker are written `D:/...`; that is
what Docker Desktop understands.

---

## Part A - Laptop to laptop

### A0. What moves, and how

A running container cannot be copied as it is. It is made of three things, and
all three can be:

| Part | What it is | How it moves | Size (Oct 2026) |
|---|---|---|---|
| Images | The built programs: api, ingestion, scheduler, drone-runner, intelligence-runner, frontend, postgres, and the pulled ones (redis, nginx, mediamtx, minio, traccar) | `docker save` into one file, `docker load` on the other side | about 5 GB (the five backend images share most of their layers) |
| Volumes | The data: the database, evidence, recordings, staff documents, guardhouse files, backups, certificates | packed with `tar`, unpacked on the other side | about 7.5 GB, evidence 5.7 GB of it |
| Settings | `docker/docker-compose.yml` (in the repository) and `docker/.env` (not in the repository) | `git clone`, and `.env` by hand | small |

Start the same images with the same volumes and the same settings, and Docker
makes the same containers.

**Not copied:**

- **The AI worker images** (11 images of 5.4 GB each). They are CPU builds and
  would never use a GPU. They are built again on the new laptop (A10).
- **Docker's own data disk** (`docker_data.vhdx`). It belongs to the old Windows
  account; Docker Desktop will not start with it on another machine.
- **`model_cache`** (downloaded again when needed), **prometheus** and
  **grafana** data (monitoring history, not product data).

**What you need:** an external SSD of 32 GB or more, formatted **exFAT or NTFS**
(FAT32 cannot hold a file over 4 GB), both laptops, access to the GitHub
repository, and about two hours.

**Do it when nobody is using the system.** Step A2 stops it.

### A1. On the old laptop - check the code is on GitHub

```bash
git status
```

```bash
git fetch origin && git log --oneline -1 origin/main
```

Nothing of yours should be uncommitted except the files listed in A5.

### A2. On the old laptop - stop the stack

Stopping keeps every container; it only makes sure the database files are not
being written while they are copied.

```bash
docker compose -f docker/docker-compose.yml stop
```

```bash
mkdir -p /d/seventh-move/by-hand
```

### A3. On the old laptop - save the images

```bash
docker save -o D:/seventh-move/seventh-images.tar docker-api docker-ingestion docker-scheduler docker-drone-runner docker-intelligence-runner docker-frontend docker-postgres docker-traccar-db redis:7-alpine nginx:alpine bluenviron/mediamtx:latest-ffmpeg minio/minio:RELEASE.2024-11-07T00-52-20Z minio/mc:RELEASE.2024-11-21T17-21-54Z traccar/traccar:6.16.0-alpine
```

### A4. On the old laptop - pack the data volumes

One `.tgz` file per volume. The database volume is copied whole, so the test
database and the application's own database user come with it; that is safe
because the same Postgres image travels in A3.

```bash
for v in postgres_data evidence_data recordings_data employee_docs_data attendance_photos_data guardhouse_data audit_archive backups_data minio_data drone_edge_data certs_data; do MSYS_NO_PATHCONV=1 docker run --rm -v docker_$v:/data -v D:/seventh-move:/backup redis:7-alpine tar czf /backup/$v.tgz -C /data . || echo "FAILED: $v"; done
```

Nothing should print `FAILED`.

### A5. On the old laptop - the files that are not in the repository

```bash
cp docker/.env /d/seventh-move/by-hand/docker.env
```

```bash
cp .env /d/seventh-move/by-hand/root.env
```

```bash
cp mobile/google-services.json /d/seventh-move/by-hand/
```

```bash
cp -r mobile-builds /d/seventh-move/by-hand/
```

Also worth taking, if they are there: the desktop installer in
`desktop/release/`, and the `backups/` folder in the repository (local, never
committed).

**`docker/.env` must be copied exactly, never re-made.** Two of its keys cannot
be changed once data exists:

- `CREDENTIALS_ENCRYPTION_KEY` - with a different one, every stored camera and
  device password becomes unreadable.
- `AUDIT_HMAC_KEY` - with a different one, every existing audit log row fails
  its integrity check.

Optional: Claude's memory and session history for this project are in
`C:\Users\<you>\.claude\projects\D--Claude-Project-Virtual-Patrolling\`. The
session history contains passwords typed in past sessions: copy it only to the
new laptop, never into the repository or anywhere shared.

### A6. On the old laptop - fingerprints, then start it again

```bash
cd /d/seventh-move && sha256sum *.tar *.tgz > SHA256SUMS
```

Start the old stack again; keep it until the new laptop is proven (A12):

```bash
cd "/d/Claude Project/Virtual Patrolling" && docker compose -f docker/docker-compose.yml start
```

Copy the whole `D:\seventh-move` folder to the external SSD.

### A7. On the new laptop - install

1. **NVIDIA driver**, 525 or newer, from nvidia.com.
2. **Git for Windows** (it brings Git Bash).
3. **Docker Desktop**, with the WSL 2 backend.
4. Give Docker enough memory. Create `C:\Users\<you>\.wslconfig` with:

   ```ini
   [wsl2]
   memory=24GB
   ```

   (for a 32 GB laptop; leave Windows about a quarter), then restart Docker
   Desktop.
5. Check Docker can see the GPU. If this does not print the card's name,
   nothing about the GPU below will work:

```bash
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

On Windows do **not** install `nvidia-container-toolkit` inside WSL; Docker
Desktop wires the GPU itself.

### A8. On the new laptop - code and settings

Use the same folder as before if you can: Claude's memory folder is named
after it.

```bash
git clone https://github.com/sathishtvg/SeventhAIVision.git "/d/Claude Project/Virtual Patrolling"
```

```bash
cd "/d/Claude Project/Virtual Patrolling" && git checkout Development
```

Copy `D:\seventh-move` from the SSD to the new laptop's `D:\`, then:

```bash
cp /d/seventh-move/by-hand/docker.env docker/.env && cp /d/seventh-move/by-hand/root.env .env && cp /d/seventh-move/by-hand/google-services.json mobile/
```

```bash
cd /d/seventh-move && sha256sum -c SHA256SUMS
```

Every line must say `OK`.

The folder holding `docker-compose.yml` must stay named `docker`: Docker
Compose names the volumes after it (`docker_evidence_data`, and so on).

### A9. On the new laptop - images, volumes, start

Load the images:

```bash
docker load -i D:/seventh-move/seventh-images.tar
```

Create each volume the way Docker Compose would, and unpack its data into it:

```bash
for v in postgres_data evidence_data recordings_data employee_docs_data attendance_photos_data guardhouse_data audit_archive backups_data minio_data drone_edge_data certs_data; do docker volume create --label com.docker.compose.project=docker --label com.docker.compose.volume=$v docker_$v && MSYS_NO_PATHCONV=1 docker run --rm -v docker_$v:/data -v D:/seventh-move:/backup redis:7-alpine sh -c "cd /data && tar xzf /backup/$v.tgz" || echo "FAILED: $v"; done
```

Start the core services. The images are already there, so nothing is built and
the containers come up as they were on the old laptop:

```bash
cd "/d/Claude Project/Virtual Patrolling" && docker compose -f docker/docker-compose.yml up -d --no-deps postgres redis frontend-certs api ingestion scheduler drone-runner intelligence-runner frontend
```

### A10. On the new laptop - check it is the same system

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/health
```

`200`. Then open `https://localhost` (the certificate is self-signed: accept
the warning once) and sign in with an account you know. Check the data came
across:

```bash
docker exec docker-postgres-1 psql -U postgres -d seventh_ai_vision -At -c "select version_num from alembic_version"
```

```bash
docker exec docker-api-1 sh -c "ls /data/evidence | head"
```

The migration is whatever the old laptop's images had (on 15 October 2026 that
is `0158`, from before the fleet work), and evidence files are listed.

### A11. On the new laptop - bring it up to date, and the AI onto the GPU

**Latest code.** Build the application images from the code, and start them.
The api applies the newer database migrations (`0159` to `0162`) when it starts:

```bash
git pull && docker compose -f docker/docker-compose.yml build api ingestion scheduler drone-runner intelligence-runner frontend
```

```bash
docker compose -f docker/docker-compose.yml up -d --no-deps api ingestion scheduler drone-runner intelligence-runner frontend
```

Bring the **test** database to the same migration, or test runs fail in ways
that look like real faults:

```bash
MSYS_NO_PATHCONV=1 docker exec -e ALEMBIC_DATABASE_URL="$(docker exec docker-api-1 printenv ALEMBIC_DATABASE_URL)_test" docker-api-1 sh -c "cd /app/backend && alembic upgrade head"
```

**AI workers on the GPU.** `docker/docker-compose.gpu.yml` reserves the GPU,
sets `DEVICE: cuda`, and builds each worker from `docker/ai-worker-gpu.Dockerfile`
under its own name ending `-gpu:local`, so the CPU images are left alone. Build
and start the workers you want, one or two at first:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.gpu.yml build ai-worker-face
```

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.gpu.yml up -d --no-deps mediamtx ai-worker-face
```

Check it really is on the GPU; a worker falls back to the CPU without saying
so:

```bash
docker exec docker-ai-worker-face-1 python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Two cautions:

- **The GPU image has never been built or run on a GPU in this project.** It
  was written for it; its first build may need fixing.
- **Graphics memory runs out before anything else.** Every worker is its own
  process with its own share of the card. Start two or three, watch
  `nvidia-smi`, then add more.

### A12. Point everything at the new laptop

| Client | What to change |
|---|---|
| Browsers | `https://<new laptop address>` |
| Desktop app | Its setup screen asks for the server address; enter `http://<new laptop address>:8000` |
| Phone app | The APK has `http://192.168.1.3:8000` built in. Either give the new laptop that address (B2), or build a new APK with the new address |
| Traccar Client on phones | Server address `http://<new laptop address>:5055` |
| Windows Firewall | Open the ports in B6 on the new laptop |

Keep the old laptop as it is until the new one has run a full day. If anything
is wrong, the old one still has everything.

### A13. Never

- Copy `docker_data.vhdx` between machines.
- Make a new `CREDENTIALS_ENCRYPTION_KEY` or `AUDIT_HMAC_KEY` for data that
  exists.
- Use Docker Desktop's "Reset to factory defaults": it deletes every volume.
- Run two test runs against the test database at once.

---

## Part B - A laptop as the server

The laptop runs the whole stack; everything else on the same network uses it.

### B1. What connects to it, and on which port

| Port | Who uses it | Needed |
|---|---|---|
| 443 | Browsers: the web console over HTTPS | Yes |
| 5173 | Browsers: the web console without HTTPS | Optional |
| 8000 | The phone app and the desktop app talk to the API directly | Yes |
| 5055 | GPS trackers and Traccar Client phones (fleet, Compose profile `fleet`) | Only with fleet tracking |
| 8554 | Another device reading a camera stream from mediamtx | Rarely |
| 8082 | Traccar's own screen | Never from outside: it listens on the laptop only |

Cameras are not connected to; the laptop connects to them (RTSP), so it must be
on the same network as the cameras or have a route to them.

### B2. Give it an address that never changes

In the router, reserve an address for the laptop (often called *DHCP
reservation* or *static lease*, by the laptop's MAC address). Reserving
`192.168.1.3` keeps the current phone APK working. Use a network cable rather
than Wi-Fi if you can.

### B3. Keep it awake

Windows Settings, then *System*, then *Power*: when plugged in, **screen off is
fine, sleep never**. Control Panel, *Power Options*, *Choose what closing the
lid does*: **Do nothing** when plugged in. Keep it on the charger; if the maker's
utility can limit charging to about 80 %, use it, as the battery will otherwise
sit at 100 % for months.

### B4. Start by itself after a restart

- Docker Desktop, *Settings*, *General*: **Start Docker Desktop when you sign
  in**.
- The application's containers are set to `restart: unless-stopped`, so they
  come back when Docker does.
- **Docker Desktop only runs after a Windows user has signed in.** After a
  Windows Update restart, somebody has to sign in before the system is back.
  This is the main reason a real server should run Linux (Part C).

### B5. Windows Update

Set *active hours* to the hours the site works, and pause updates for a demo or
pilot week. Restart it yourself, at a time you choose, and check A10 afterwards.

### B6. Windows Firewall

The network must be set as **Private** (Settings, *Network*, the connection,
*Network profile type*). Then, in PowerShell **as administrator**, open only
what B1 needs, and only to the private network:

```powershell
New-NetFirewallRule -DisplayName "Seventh AI web and API" -Direction Inbound -Protocol TCP -LocalPort 443,8000 -Action Allow -Profile Private
```

```powershell
New-NetFirewallRule -DisplayName "Seventh AI GPS trackers" -Direction Inbound -Protocol TCP -LocalPort 5055 -Action Allow -Profile Private
```

These are security settings of the laptop: make them yourself, and only for the
ports you use.

### B7. HTTPS certificate

On first start the web console makes itself a self-signed certificate, and each
browser warns about it once. For a site, put a real certificate in the
`certs_data` volume as `server.crt` and `server.key` (the names in
`docker/nginx.conf`) and restart `frontend`.

### B8. Backups

The scheduler writes a database backup every day into the `backups_data`
volume and keeps the last seven. That protects against a mistake, not against
losing the laptop. Once a week, copy the backups and the evidence off it, with
the same command as A4 but only for `backups_data` and `evidence_data`, to a
drive kept elsewhere. Restoring is in `docs/DR-RUNBOOK.md`.

### B9. Reaching it from outside the site

Do **not** open ports on the router to the internet. Use a VPN instead:
Tailscale or WireGuard put the laptop on a private network with your own
devices; Cloudflare Tunnel can publish the web console alone without opening
any port. Any of them keeps the API and the cameras off the open internet.

### B10. What a laptop server is good for

Demos, a pilot, one small site. It is one machine with one disk, built to be
carried rather than run day and night, and Windows restarts it for updates.
For a customer who depends on it, use Part C.

---

## Part C - Where to deploy it

### C1. The recommendation, in short

| Stage | Run it on | Why |
|---|---|---|
| Now: demos and a first pilot | **The GPU laptop as the server** (Part B) | Costs nothing more; cameras and phones are on the same network |
| Demos from anywhere | **One cloud GPU machine in Singapore**, switched on only when needed | A public web address without carrying hardware; billed by the hour |
| Paying customers | **One Linux server with an NVIDIA GPU at each customer's site** | Video stays on site, nothing is uploaded, it keeps working when the internet does not |
| Many customers, later | **Kubernetes in the cloud**, with the repository's Helm chart | One installation for many organisations - but it is a project of its own (C5) |

### C2. A cloud GPU machine (demos from anywhere)

Any of the three large clouds has GPU machines in its Singapore region. Sizes
that fit the whole stack (about 32 GB of memory and a 16 to 24 GB GPU):

| Cloud | Region | Machine | GPU |
|---|---|---|---|
| AWS | ap-southeast-1 (Singapore) | g4dn.2xlarge | NVIDIA T4, 16 GB |
| Microsoft Azure | Southeast Asia (Singapore) | NC8as T4 v3 | NVIDIA T4, 16 GB |
| Google Cloud | asia-southeast1 (Singapore) | g2-standard-8 | NVIDIA L4, 24 GB |

If you have no reason to prefer one, start with **AWS in Singapore on a
g4dn.2xlarge**. Install **Ubuntu Server 22.04 LTS**, then
`sudo bash scripts/gpu-setup.sh` (driver, NVIDIA Container Toolkit, Docker), then
Part A from A8, with the Linux paths in place of `D:/...`.

Worth knowing before you start:

- **A new account can start no GPU machine** until you ask the cloud for a GPU
  quota; the answer can take a day or two. Check too that the machine in the
  table is offered in the Singapore region on the day: what each cloud offers
  where changes.
- **Billing is by the hour while it is running**, and the disks are billed even
  while it is stopped. Stop it after each demo. Check the current price on the
  cloud's own calculator before you start one.
- **It cannot see a customer's cameras.** They are on the customer's network.
  For demos, use recorded clips played through mediamtx.
- Give it a domain name and a real certificate (B7), and keep port 8000 behind
  the web server or a VPN rather than open to everyone.

### C3. A server at each customer's site (paying customers)

What the stack runs on today, unchanged: Docker Compose on one machine.

- **Hardware:** a tower workstation or small server; an NVIDIA RTX card with
  12 GB or more of graphics memory; 32 to 64 GB of memory; an NVMe SSD for the
  database; a separate disk for recordings and evidence, sized for how long the
  customer keeps them; a UPS.
- **System:** Ubuntu Server 24.04 LTS, `scripts/gpu-setup.sh`. Unlike Windows it
  needs nobody to sign in after a restart, and the GPU works in Docker without
  Docker Desktop.
- **Support from afar:** a VPN (B9), never an open port.
- **Why on site:** the cameras are on the customer's network; streaming them all
  to a cloud costs bandwidth, needs openings in their firewall, and takes video
  out of the building, which matters under the PDPA.
- **How many cameras one server can watch** has not been measured. Measure it
  in the pilot before promising a number.

### C4. Singapore

- Keep every installation's data in Singapore: a Singapore region in the cloud,
  or on the customer's premises.
- A public-sector customer may require Government on Commercial Cloud (GCC),
  which is offered on AWS, Azure and Google Cloud. Ask before choosing.

### C5. Kubernetes, later, and what it would take

The repository has a Helm chart (`helm/seventh-ai-vision`). CI checks that it
renders; it has **never been deployed** to a cluster. Moving to a managed
Kubernetes service (EKS, AKS or GKE) would also mean:

- A managed database that offers both **pgvector** and **pg_partman**, which
  the database uses.
- Reworking the jobs that run on a superuser database session (partition
  maintenance, retention): managed databases do not give a true superuser.
- Getting video from the sites. Only Drone Patrol has a site box that sends its
  results to a central server (the drone edge gateway); cameras do not, yet.

Worth it when there are enough customers that running one server each costs
more than building that.


---

## Part D - The AWS demo server, step by step

Prepared on 15 October 2026 and **not yet run against AWS**: the two scripts
below are checked for syntax and nothing more. Expect the first run to need a
fix or two.

- `scripts/aws/aws-demo.sh` runs on your laptop, in Git Bash, from the
  repository folder. It makes, starts, stops and removes the machine.
- `scripts/aws/server-setup.sh` runs on the machine. `aws-demo.sh setup` sends
  it there and runs it.

### D1. Which machine

| Mode | Machine | What it shows | What it costs |
|---|---|---|---|
| `free` (the default) | m7i-flex.large: 2 processors, 8 GB, no GPU | Everything except the AI analytics: the console, rosters, attendance, visitors, fleet, alerts, the cameras' live view | It is on AWS's free-plan list. While the account stays on the Free plan, it is paid from the sign-up credit, not your card. The 40 GB disk and the internet address are counted too |
| `gpu` | g4dn.xlarge: NVIDIA T4 | Everything, with two AI modules (intrusion, face) to start | About US$0.74 an hour while running. Not on the free list; AWS may ask for the paid plan |

For `gpu`, put `SEVENTH_MODE=gpu` in front of each command, for example
`SEVENTH_MODE=gpu scripts/aws/aws-demo.sh create --yes`.

On the free machine, continuous recording of the demo cameras is switched off
after the demo data is loaded. Recording three cameras is what overloaded an
8 GB laptop on 8 October 2026; a 2-processor machine has less to spare.

### D2. What only the account's owner does (about 15 minutes)

1. Create the AWS account at aws.amazon.com and choose the **Free plan**.
2. Turn on two-factor sign-in for the account's root user.
3. Billing, then *Budgets*: a budget of US$1 with an e-mail alert, so any spending
   at all is told to you.
4. IAM: create a user for deployment, with the policies **AmazonEC2FullAccess**
   and **ServiceQuotasFullAccess**, and an access key for the command line.
5. In Git Bash (the AWS tool is installed on the development laptop):

```bash
aws configure
```

Type the access key and its secret when asked, region `ap-southeast-1`,
output `json`. They stay on your laptop, in `C:\Users\<you>\.aws`.

### D3. Make it, set it up, use it

```bash
scripts/aws/aws-demo.sh create --yes
```

Makes a login key (saved to `C:\Users\<you>\seventh-aws\`, the only way into
the machine: keep it), a firewall open to your laptop's internet address only,
and the machine. It starts running.

```bash
scripts/aws/aws-demo.sh setup
```

Installs Docker, fetches the code from GitHub, makes the application's
secrets on the machine (they never leave it), builds and starts everything,
and loads the demo organisation with a new password. The first run takes a
while. At the end the demo accounts and their password are saved to
`C:\Users\<you>\seventh-aws\demo-accounts.txt`, and the address is printed:
open `https://<address>` and accept the certificate warning once.

```bash
scripts/aws/aws-demo.sh down
```

Stops the machine after the demo. Before the next one:

```bash
scripts/aws/aws-demo.sh up
```

It starts the machine, moves the firewall to wherever your laptop is now, and
prints the address. The address changes each time it starts.

Also: `today` and `live` (the demo story, D4), `status`, `ssh` (a terminal on
the machine), `password` (fetch the demo accounts file again), `destroy --yes`
(remove the machine and its disk for good).

### D4. Good to know

- **Only your laptop can reach it.** The firewall lets in one internet address,
  your laptop's at the last `create` or `up`. A prospect watches your screen;
  they cannot open it from their own device.
- **The demo password in the repository is not used.** The repository is
  public, and so is the password written in `backend/scripts/seed_demo.py`. The
  server's demo accounts get one made on the server.
- **Today's story goes stale.** On the morning of a demo run
  `scripts/aws/aws-demo.sh today` (today's shifts, visits and incidents), and
  five minutes before you start `scripts/aws/aws-demo.sh live` (an incident to
  dispatch and a situation to decide), as `docs/DEMO-SCRIPT.md` describes for
  the laptop. They use the server's own demo password.
- **The Free plan ends** after six months or when the credit is used up. Before
  then, decide whether to move to the paid plan, and check in the billing
  console what happens to the machine and its data if you do not.
