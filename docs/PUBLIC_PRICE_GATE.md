# Public price barrier

Price information is forbidden, including empty/pending price fields. Public scope approval,
deletion approval, valid signatures, rollback and a durable pending marker do not waive this rule.
The publisher rejects data; it does not remove records or rewrite technical content.

## Enforcement

- `collect` / `fetch` / CLI `audit`: validate committed Git blobs, full logical paths,
  Markdown body, tables and decoded YAML keys/values/tags.
- `Store.prepare`: validate programmatic snapshots and report metadata before writing objects.
- `Store.read`: inspect the entire outgoing `public/` and `state/` trees, including orphan
  objects, retained manifests/reports and old ZIP payloads, then retain existing signature,
  size/hash and Markdown validation of the selected release.
- Pending resume, promote, complete, rollback, package and mirror deployment use `Store.read`;
  legacy signatures cannot bypass the present-day barrier.
- `ci_state.checkpoint`: inspect both trees again **before** Git add/commit/push. This is the
  last mandatory check before the workflow uploads `store/public` to Pages.
- HTTP verification and Python sync/import check control metadata and file contents before
  activation, including cache reuse and same-version generations.

The scanner recognizes Chinese price/quotation/unit/deal/supply/purchase/cost/tax-price,
discount and rebate labels, corresponding common English labels, and explicit currency/number
expressions. It scans an NFKC/format-character-normalized view without changing original bytes.
YAML escapes and JSON escapes are decoded for inspection. Technical temperature, percentages,
viscosity, acid-value units, physical measurements and failed technical-test data remain intact.

Errors contain a category and SHA-256 fingerprint of the logical path, never matched text or
literal filenames (filenames can themselves contain amounts). Locate a rejected path locally by
hashing its UTF-8 spelling. No price override flag exists. Unreadable/oversized outgoing payloads
fail closed. Signature files are validated as canonical Ed25519 signature representations rather
than scanned as natural-language content; the original cryptographic protocol is unchanged.

## Limits and operational requirements

This is a deterministic lexical price barrier, not semantic anonymization or OCR. The separate
public-data generator must still use its field whitelist to omit private recipes and customer
identities. Do not publish a former private Git/distribution history: scanning current trees does
not erase earlier commits. Start from an independently approved clean public distribution.
Retained legacy resources containing prices intentionally block publication; never deploy a
pending store merely because the selected new manifest appears clean. No production store or
private source is modified by the regression tests. Test fixtures are synthetic.

Run: `python -m pytest -q` (CI: Python 3.12, Linux and Windows).
