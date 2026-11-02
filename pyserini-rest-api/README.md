# Pyserini REST API Access

CMASS uses the same ClimbMix corpus and hosted BM25 API as TREC RAG. If you already
have a Pyserini API token, you can reuse it for CMASS without requesting another.
The hosted API is optional; a compatible local index can also be used.

This guide replicates the emailed-token workflow from the
[Pyserini REST guide](https://github.com/castorini/pyserini/blob/master/docs/usage-rest.md#api-token-requests-and-email-delivery)
and the [API skill](https://github.com/TREC-RAG/trec-rag-skills/blob/main/skills/pyserini-rest-api/SKILL.md)
for CMASS users. The current service is:

```text
https://api.castorini.uwaterloo.ca
```

Use the `climbmix-400b` index for every CMASS benchmark. The
[interactive API documentation](https://api.castorini.uwaterloo.ca/docs) lists the
available endpoints and parameters.

## Request a Token

Send your real name and email address to `POST /v1/token`. Replace the placeholder
values before running this command; agents should ask for either value if it is
not available.

```bash
curl --fail-with-body --silent --show-error \
  -X POST 'https://api.castorini.uwaterloo.ca/v1/token' \
  -H 'Content-Type: application/json' \
  --data '{"name":"Your full name","email":"you@example.edu"}'
```

A successful request returns **HTTP 202**:

```json
{"status":"accepted","message":"Token delivery will be sent by email."}
```

The token is sent to your email address, never returned in the HTTP response.
The service assigns a pre-generated token and records its owner; it does not
generate credentials on the server.

Each normalized email address has one token for its lifetime. A later eligible
request resends that same token. Independent one-hour cooldowns apply to the
client IP and email address: a request is accepted only when both are outside
their cooldowns. Researchers sharing a public IP also share that IP's cooldown.

Token emails are sent from `castorini.api@gmail.com` and CC these administrators:

- `lingwei.gu@uwaterloo.ca`
- `njedidi@uwaterloo.ca`
- `jimmylin@uwaterloo.ca`
- `l2ge@uwaterloo.ca`
- `sahel.sharifymoghaddam@uwaterloo.ca`

Treat the sender as a no-reply mailbox. For help, use **Reply all** and address
the CC'd administrators.

## Store the Emailed Token

Keep the token out of chat, prompts, source code, logs, shell history and Git.
Create a private curl configuration file in the repository:

```bash
umask 077
touch .curlrc.pyserini-rest
chmod 600 .curlrc.pyserini-rest
```

Using your local editor, put the following line in that file, replacing the
placeholder with the token from your email:

```text
header = "Authorization: Bearer <your-emailed-token>"
```

CMASS ignores `.curlrc.pyserini-rest` and `.env.local`. Never commit credential
files or print their contents. Using `curl -K` below keeps the credential out of
the request's command-line arguments. `X-API-Key` is also supported as an
alternative authentication header.

## Search ClimbMix

The following is a general example, not a CMASS benchmark question:

```bash
curl --fail-with-body --silent --show-error \
  -K .curlrc.pyserini-rest \
  --get 'https://api.castorini.uwaterloo.ca/v1/climbmix-400b/search' \
  --data-urlencode 'query=information retrieval' \
  --data-urlencode 'hits=5' \
  --data-urlencode 'max_doc_length=500'
```

Results are returned in `candidates`, with `docid`, `score`, `rank` and `doc`
fields. To read a returned document, replace `<docid>` below with its identifier:

```bash
curl --fail-with-body --silent --show-error \
  -K .curlrc.pyserini-rest \
  'https://api.castorini.uwaterloo.ca/v1/climbmix-400b/doc/<docid>'
```

By courtesy, agents should include the optional `qid`, `question`, `run_id`,
`agent` and `step` query parameters whenever their values are known. These
fields and retrieval queries are collected solely for academic research on
agent retrieval behavior; they do not affect ranking. Use a dataset-qualified
`qid`, keep `run_id` stable for one answer attempt, and increment `step` from
zero for each search or document fetch. Do not invent a question or identifier
when none is available. URL-encode all query parameters.

The [reproduction guide](../REPRODUCING.md#piika-retrieval) specifies the search
previews and tool behavior needed for comparisons with the paper.

## Errors and Request Pacing

| Status | Meaning | Action |
| --- | --- | --- |
| `400` | Missing or invalid request fields. | Check the name/email or retrieval parameters. |
| `401` | Missing or invalid API token. | Check the private credential file without displaying it. |
| `429` | Token-request cooldown or temporary retrieval load shedding. | Honor `Retry-After` and reduce request concurrency. |
| `503` | Token delivery is disabled, unavailable or failed. | Wait and contact the administrators if it persists. |
| Other `5xx` or timeout | Temporary service or network failure. | Use bounded exponential backoff. |

Keep a fixed, modest worker pool with at most one request in flight per worker.
Back off on `429`, transient `5xx` responses and timeouts; never retry in a tight
loop. A healthy root endpoint alone does not prove retrieval works: also check
that an authenticated search returns `200` with at least one candidate.
