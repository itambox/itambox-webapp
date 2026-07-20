# API Tokens

An **API Token** is a bearer credential used for authenticating REST API requests. Tokens are scoped to a specific user and tenant, may be time-limited with an expiry date, and can be restricted by source IP ranges. The plaintext secret is shown exactly once at creation time and is never stored in the database.

---

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **User** | The Django user account the token authenticates as. | Foreign Key | Yes |
| **Tenant** | The tenant scope of the token. Defaults to the ambient request tenant at creation time. | Foreign Key | Yes |
| **Description** | Human-readable label for the token (e.g. "CI/CD pipeline", "Lansweeper integration"). | String (200) | No |
| **Write Enabled** | Whether the token grants write (POST/PUT/PATCH/DELETE) access in addition to read. Defaults to `True`. | Boolean | Yes |
| **Expires** | Optional expiry date. Tokens without an expiry never expire. | DateTime | No |
| **Created** | Timestamp when the token was generated (auto-set). | DateTime | Yes |
| **Last Used** | Timestamp of the most recent authenticated request using this token. Updated on each use. | DateTime | No |
| **Allowed IPs** | Array of permitted IPv4/IPv6 networks in CIDR notation (e.g. `["192.168.1.0/24", "10.0.0.5"]`). Leave blank to allow any source address. | Array (String) | No |
| **Key Preview** | First 8 characters of the plaintext token, stored for identification in the admin UI. Not a secret. | String (16) | No |

---

## Security Model

### Key Hashing

The plaintext token is **never stored**. At creation time:
1. A 40-character hex secret is generated via `secrets.token_hex(20)`.
2. It is combined with a server-side **pepper** (rotatable secret configured via `ITAMBOX_API_TOKEN_PEPPERS`) using HMAC-SHA256.
3. Only the digest and pepper ID are persisted — the plaintext is returned once and discarded.

Token lookup (`Token.find_by_key`) compares the presented plaintext against HMAC digests computed with every configured pepper, supporting zero-downtime pepper rotation.

### IP Restrictions

When `allowed_ips` is configured, each authenticated request is validated against the client's remote address. An empty list imposes no restriction. Unparseable client IPs or IPs outside every configured prefix are **rejected** (fail-closed).

### Expiry

Tokens with an `expires` timestamp in the past are rejected at authentication time. Tokens without an expiry are valid indefinitely until manually revoked.

## Changelog Exclusion

The credential material (`digest`, `pepper`) and the high-frequency `last_used` heartbeat are excluded from the changelog to avoid leaking secrets into the audit trail.
