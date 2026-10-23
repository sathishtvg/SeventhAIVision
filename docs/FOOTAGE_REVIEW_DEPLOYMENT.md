# Footage Review — deploying it: SaaS on AWS Singapore, or an agency's GCC account

Requirement 5 of `FOOTAGE_REVIEW.md`: *SaaS hosting or Singapore Government
Commercial Cloud (GCC), where supported.* This is the package for both. **It is
prepared and has never been run:** no account, bucket, key or cluster has been
created, and none may be without the owner's yes.

What the package is:

| File | What it is |
|---|---|
| `helm/seventh-ai-vision/templates/footage-worker-deployment.yaml` | The footage worker: non-root, read-only file system, no added capabilities, health checked |
| `helm/seventh-ai-vision/templates/footage-worker-service.yaml` | Its port 8090, inside the cluster only |
| `helm/seventh-ai-vision/templates/footage-worker-networkpolicy.yaml` | Only the API may reach that port (on in the GCC values) |
| `helm/seventh-ai-vision/templates/footage-ingress.yaml` | The upload path alone may send 16 MB parts |
| `helm/seventh-ai-vision/values-gcc.yaml` | What differs in an agency's GCC account |
| `scripts/aws/footage/s3-lifecycle.json` | Abandons an unfinished multipart upload after a day |
| `scripts/aws/footage/s3-encryption.json` | Every object encrypted with the agency's KMS key |
| `scripts/aws/footage/s3-bucket-policy.json` | TLS only, and only through the S3 VPC endpoint |

## Part A — SaaS on AWS Singapore

For private customers. Everything in `ap-southeast-1`.

1. **Images.** Build and push the four images from a tagged release, from the
   repository, not a working copy. The footage worker's build downloads its
   models once (SigLIP 2 from Hugging Face, pinned and checked; the pose model
   from Ultralytics' release, checked) and needs PyTorch only in a build stage.
2. **Storage.** One S3 bucket, versioning off, with `s3-encryption.json` (KMS),
   `s3-lifecycle.json` and `s3-bucket-policy.json`. **There is deliberately no
   lifecycle rule that deletes footage:** the application's daily job removes a
   video after 30 days and leaves one under an evidence hold; a bucket rule
   would delete held footage too.
3. **The chart**, with `config.storageBackend: s3` and the bucket. Without
   Kubernetes, the single-server Compose stack works the same way: start the
   worker with `--profile footage`.
4. **Workers.** One replica analyses one video at a time. On the development
   machine a 15-second test video took 11 to 28 seconds on 2 CPU threads; how
   long an hour of real footage takes is to be measured. Run more replicas for
   more uploads. A GPU is optional and not needed.
5. **The upload path.** The ingress lets `/api/v1/footage/` alone take 17 MB
   bodies (`proxy-body-size`), as `docker/nginx.conf` does on a single server.

## Part B — An agency's GCC account

GCC 2.0 runs on AWS, Azure and Google Cloud; this package is for AWS. A
government buyer's footage is unlikely to be classified low-risk, so the
platform runs **in the agency's own GCC account**, not in our SaaS.

**Who does what.** The agency (or its integrator) onboards through GovTech's
TechPass, owns the account, the cluster, the database, the bucket and its key,
and runs the GCC compliance checks. We supply the images, the chart with
`values-gcc.yaml`, this guide, and the controls sheet below.

1. **No internet.** A GCC workload has no outbound internet. The images are
   pushed to the agency's own registry (ECR), and every model is already in the
   footage worker's image; nothing is fetched when it runs.
2. **Private endpoints** for S3, ECR, KMS, STS and CloudWatch Logs, so nothing
   leaves the VPC. The bucket policy refuses access except through the S3
   endpoint.
3. **Identity.** The pods use an IAM role through the service account (IRSA,
   `serviceAccount.annotations` in `values-gcc.yaml`), allowed the bucket and its
   key and nothing else. Leave `s3.accessKey` and `s3.secretKey` empty: with no
   key the API, the AI workers and the footage worker all use the role (Part D).
4. **The database.** A managed PostgreSQL 16 with **pgvector** (search) and
   **pg_partman** (partitions). Two jobs run as the database's superuser
   (partition maintenance and the retention removals, Footage Review's
   included); a managed database gives no true superuser, so those need a
   decision with the agency (a dedicated role with the grants they need).
5. **Network.** `footageWorker.networkPolicy.enabled: true` lets only the API
   reach the worker; the ingress is the agency's internal one, over TLS.
6. **Malware scanning of uploads.** The platform never runs an uploaded file —
   it only decodes it with ffmpeg in the worker's container, which has no
   outbound internet — but an agency may require uploads to be scanned. AWS's
   malware protection for S3 can scan the bucket; it is configured in the
   agency's account, not built into the application.
7. **Logs.** Container logs to CloudWatch through its endpoint. The words of a
   search are not written to any log; they are in the application's audit log.

## Part C — Controls sheet

What the application does, against the topics a Singapore government security
review covers (IM8). **This maps; it does not certify.** The assessment is the
agency's, with its own tools.

| Topic | What the application does | Where | The agency's part |
|---|---|---|---|
| Access | Four permissions (`footage:upload`, `:view`, `:review`, `:delete`); site limits; another organisation's video answers as not found; the platform owner holds none | `FOOTAGE_REVIEW.md`, *Who may do what* | Who gets which role |
| Data in transit | TLS at the ingress; the bucket refuses plain HTTP | `values-gcc.yaml`, `s3-bucket-policy.json` | The certificate |
| Data at rest | S3 server-side encryption with the agency's KMS key | `s3-encryption.json` | The key and its policy; the database's encryption |
| Where data is | Singapore region only | `values-gcc.yaml` | The account's region |
| Retention | 30 days from upload, then removed with everything worked out from it; an evidence hold keeps it; each removal audited | `FOOTAGE_REVIEW.md`, *Keeping and removal* | Whether 30 days is right for the use |
| Audit | Uploads, playback, searches with their wording, verdicts, holds and deletions in the hash-chained audit log | `backend/app/services/audit.py` | Where audit logs are kept |
| Network | No outbound internet needed; the worker's port reachable from the API alone; private endpoints | `footage-worker-networkpolicy.yaml` | The VPC, its endpoints, the ingress |
| Uploaded files | Accepted kinds only; opened with ffprobe first and refused if not a video; never executed | `backend/app/services/footage.py` | Malware scanning of the bucket |
| Supply chain | Models pinned to one revision and checked against published SHA-256s; images built from the repository | `ai-worker/worker/footage/fetch_model.py`, `docker/footage-worker.Dockerfile` | Scanning the images in its registry |
| Privacy | Recordings are masked when recorded, so a copy analysed here is masked; every search is audited | `PRIVACY_MASKING.md` | Its data-protection assessment |
| AI decisions | The models rank and flag; a person decides. Nothing found raises an alert or an incident; a possible altercation is never more than that, and a person gives the verdict | `FOOTAGE_REVIEW.md` | Whether to rely on it, once measured |

## Part D — Before it runs on a real bucket

Two were changes to existing code, made with the owner's yes on 23 October
2026; one is still the owner's:

1. **The chart's S3 keys — done.** With MinIO off, `templates/secret.yaml` now
   takes `s3.accessKey` and `s3.secretKey` from the values (empty by default),
   for an S3-compatible store that needs keys. Give them from the agency's
   secret store at install (`--set`, or a values file kept out of the
   repository); on AWS leave them empty and use the role.
2. **The S3 clients and a role — done.** `backend/app/core/object_store.py` and
   `ai-worker/worker/storage.py` now treat an empty key or endpoint as none, so
   boto3 uses the pod's IAM role and AWS's own endpoint. The footage worker
   already did this.
3. **The Ultralytics licence.** The person, vehicle and pose detectors are
   AGPL-3.0; Ultralytics states that SaaS and cloud use needs its Enterprise
   licence.
