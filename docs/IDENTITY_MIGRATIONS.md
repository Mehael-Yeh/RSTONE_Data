# Exact identity-migration approvals

The identity gate distinguishes **retaining each complete identity** from merely
retaining a family count. It does not infer production status from a similar
number, an experimental field, matching properties, or a source-provided alias.

## Policy and trust boundary

`config/identity_migrations.json` belongs to the **reviewed publisher checkout**:

```json
{"schema":"rstone-identity-migrations/1","approvals":[]}
```

The shipped list is empty. An exception requires an owner-reviewed change to this
file, deployed together with the reviewed publisher. There is intentionally no
CLI flag, environment variable, Snapshot attribute, source-document directive or
dispatch input that can supply identity approvals. Missing/malformed policy,
unknown keys, duplicate JSON keys, duplicate receipts, wildcards and noncanonical
hashes fail closed.

The existing publication workflow checks out the publisher separately from both
private source repositories and runs only on the publisher's default branch.
`collect()` ingests allowlisted Git blobs, not source working-tree bytes or the
source repository's `config/`. Every real publication gate resolves the policy
relative to its own installed module, not the source paths. Whoever can modify
the installed publisher already controls executable gate code; publisher review,
filesystem ownership and branch protections remain required trust anchors. A
SHA-256 receipt binds an approval; **the hash itself is not authentication**.
Repository owners must enforce review of policy changes just as for gate code.

## What exactly is approved

Use the existing protocol's `canonical_json` encoding: UTF-8, sorted keys,
`ensure_ascii=False`, separators `(',', ':')`, no NaN, and a final LF. Let `H` be
lowercase SHA-256 of bytes and `C` be that canonical encoding.

```text
baselineSha256 = H(C({logical_path: H(actual_baseline_bytes), ...}))
receipt = H(C({
  "schema": "rstone-identity-migrations/1",
  "baselineSha256": baselineSha256,
  "source": {"path": old_logical_path, "sha256": H(old_actual_bytes)},
  "target": {"path": new_logical_path, "sha256": H(new_actual_bytes)}
}))
```

The baseline inventory includes **every published document in every namespace**.
Its bytes come from the exact earlier, verified signed release selected by
`Store`, not an editable audit description. The candidate bytes come from the
commit-pinned publication Snapshot. Both old/new complete paths and blob SHA-256
values participate. A change to any baseline file, either path, or the target
bytes invalidates the approval. Source commit IDs are not substitutes for these
byte checks; an identical payload at a new commit is still the same approved
payload. No timestamp, amount, personal detail, chat evidence or internal audit
file is read or persisted by this mechanism.

Owners must first establish each relation using their private evidence, then
review the exact old/new payload and baseline and independently compute the
receipt. Do not generate an automatically accepted policy from experimental
fields. Keep the evidence ledger private. Retain receipts required to revalidate
retained releases; removing one intentionally fails re-admission closed. A
changed candidate or baseline requires renewed approval, not baseline rewriting.

## Non-negotiable structural constraints

A receipt is necessary, not sufficient:

- The old complete RD stem must occur once in the baseline and disappear from
  the current products; a moved/retained RD is not migration credit.
- There must be exactly one current claimant, with scalar `实验牌号` **exactly**
  equal to that complete old stem. Bare numbers, whitespace, case folding,
  wiki-link spellings, lists and multiple aliases do not match.
- That claimant must be a unique canonical RS identity in the same product
  directory. Duplicate stems/case variants and competing claims remain defects.
- If the numeric part stays the same, every remaining suffix byte must stay
  identical: `RD9401-6B` may map to `RS9401-6B`, never `RS9401`, `RS9401-6`,
  `RS9401-8B` or a differently cased/versioned spelling. A receipt cannot
  override this rule.
- An unrelated new number is **never inferred**. A separately reviewed exact
  source/target receipt is required for that particular one-to-one renumbering;
  it grants no authority to a sibling, later blob or another baseline.
- A target's single exact experimental field cannot serve two old identities.
  Losing a different old family member is still blocked, even if the candidate
  adds unrelated suffixes to pad the family count.

Without receipts, all original suffix protections remain. A disappearing RD
with an explicit claimant or same-complete-number RS replacement is rejected,
even when the old family has only one member. Omitting the experimental field
cannot hide a fresh RS replacement: deleting a canonical RD while adding a new
canonical RS is conservatively treated as an ambiguous identity operation and
requires review. Consequently, an unrelated RD deletion and RS addition may need
to be separated into independently reviewed releases; the gate does not guess
that they are unrelated. A unique, byte-identical old-RS → new-RS move is not
classified as a fresh RD replacement when exactly one deleted formal document
and exactly one added formal document share those bytes; retained-document
copies, multiple old/new matches and changed bytes do not qualify. This grants
no RD migration credit and does not disable any suffix-family check. Pure
deletions without replacement/claims remain under
the pre-existing identity-family and exact bulk-deletion rules.

## Complete-name detection and additive bases

Detection is not approval eligibility. Ingestion accepts every case-insensitive
RD/RS-prefixed original name (including legacy spellings outside its external-
grade character class). Detection uses the same `name.upper().startswith(...)`
boundary as ingress, including Unicode expansions; testing only the first two
uppercased characters would miss accepted spellings such as `Rß9502`. Every such
old RD and new RS participates in ambiguous
replacement detection. No nonmatching spelling silently opts out. Original
paths, case, spaces, punctuation and bytes are never normalized.

Exact migration credit supports uppercase `RD`/`RS` immediately followed by an
ASCII number, with **all remaining suffix bytes preserved verbatim**. This
includes dotted, letter/version, underscore, parentheses, space and plus suffixes.
Other ingestible forms (e.g. `RD 9402`, `RDword9402`, lowercase prefixes or names
without an immediate ASCII number) remain valid unchanged inputs, but requesting
migration credit for them fails closed, even with a receipt. Number parsing never
runs on those unsupported forms. This does not narrow source-name admission.

Suffix loss and family coverage compare the complete numbered base, not a bare
string prefix: `RS94` and `RS9402` are independent. Losing a dotted, letter or
version suffix is as invalid as losing a hyphen suffix; replacing a singleton
with a different suffix of the same numbered base is also rejected. Case-only
rewrites and same-number RD-to-RS suffix byte changes remain forbidden even with
receipts. The family check preserves exact members, not just counts.

An independently added base is not a suffix deletion **only when every affected
old complete identity still exists verbatim, or individually qualifies for its
exact approved migration**. A missing sibling, shared target, invalid receipt,
changed bytes or approximate alias still fails. No business-evidence file is read
by this rule. Policy, public-scope and bulk-deletion approvals stay separate.

The local HTTP demo uses an external synthetic `X-COPY1` for its same-byte-copy
exercise; adding `RS-COPY` during an unapproved RD deletion had relied on the old
incomplete-name detection gap. The demo/client assertions are unchanged; the
publisher must reject that ambiguous RD/RS operation, not exempt demo names.

## Other gates and publication exits

Approval only credits a disappeared identity when evaluating exact family-member
coverage. It does not rewrite/normalize any documents, skip other checks, lower
declaration/deletion thresholds, or approve public scope. It does not authorize
parameter inheritance between suffixes. Tags, tag/field consistency, field
residue, substance, notes, links, price and secret checks execute as before.
Personal-field review warnings from ingestion remain warnings; this change does
not turn that pre-existing review policy into a comprehensive PII blocker.

`prepare`, pending `resume`, `promote`, `complete`, and `package` all reload the
same installed policy through the existing mandatory `validate_snapshot` gate.
No per-call exemption or unsigned pending receipt was added. `release.py`, its
baseline-selection logic, the wire manifest and deletion approvals are unchanged.
Rejection precedes signing, object writes, pending creation and ZIP creation.
Where a baseline already exists, tests assert **zero new artifacts** and
byte-identical prior state, not the impossible absence of baseline objects.

Rollback retains its existing narrow rule: only byte-identical content of the
verified signed release may be restored, with its own reference baseline. It is
not a means of admitting a new migration. Content/secret checks still run, and a
mismatching rollback Snapshot is refused before signing. No new use of
`identity_baseline=False` was introduced.

## Verification

All new public fixtures use synthetic identities. Run:

```sh
python3 -m pytest -q tests/test_identity_migrations.py \
  tests/test_identity_migration_security.py tests/test_identity_migration_cli.py
python3 -m pytest -q
```

The regressions reach real Store and CLI paths, including Ed25519 signatures,
commit-pinned local Git ingestion, policy installation, restart/resume,
revalidation of unapproved legacy pending snapshots, negative content injection,
zero signer calls and zero new publication artifacts. The CLI test demonstrates
that a source-supplied policy file and an invented approval environment variable
cannot grant authority, while an owner-installed exact policy can. Original
release tests continue exercising rollback's signed-byte constraint.

Production deployment still requires owner review of exact receipts for the
actual upstream candidate and current signed baseline. Passing local acceptance
with an isolated approved policy is not installing it on the default branch or
publishing business data.
