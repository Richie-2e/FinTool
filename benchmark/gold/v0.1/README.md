# FinTool Gold Benchmark — v0.1

Human-verified ground truth for the `CORE_METRIC_SET` (8 canonical metrics) across 5 real
annual-report documents, built by independently reading the original source PDFs — never
derived from pipeline output, prior experiment results, or the classifier's own scope
guesses.

## What "correct" means here

Every `expected_*` field was determined by opening the actual source PDF page(s) and reading
the value directly, following the annotation protocol established across five pilots
(OFSS, Reliance, LICHSGFIN, JioFin, TataSteel). `extraction_outputs/`, B1/B2/C1/C2/C2.1
experiment results, and any other pipeline artifact were used only as navigation pointers —
to know which page to open — never copied as a gold value, label, year, or scope.

## Status vocabulary

- `valid` — exactly one correct value is establishable from the source.
- `missing` — the metric genuinely does not exist in the source for this document (e.g.
  `current_assets`/`current_liabilities` for NBFC-classified filers that use a
  Financial/Non-Financial balance-sheet split instead of Current/Non-Current).
- `ambiguous` — the source presents the metric with a genuine, defensible multi-way choice
  (e.g. "Profit for the Year" vs. "Net Profit Attributable to: Owners of the Company" for a
  consolidated filer with non-controlling interests). The `candidates` array holds every
  plausible reading.
- `conflicting` — two or more materially different values both purport to be the same
  metric/year with no source-supported way to distinguish them. **No record in v0.1 carries
  this status** — the one case that originally looked like this (Reliance's
  total_liabilities/total_equity) was found, on independent source verification, to be a
  pipeline year-tagging defect, not a genuine source conflict; see that record's
  `known_failure_class` and `notes`.
- `unresolved` — the labeler could not determine the answer from available material. None in
  v0.1 (every page needed was legible and locatable).

## Schema

See any record in `gold_records.jsonl` for the full field set. Two fields were added after
the OFSS pilot and are present throughout: `source_has_no_explicit_label` (true when the
source row has no distinct label text of its own) and `derived`/`derivation_formula` (used
when a gold value is computed from two or more source figures rather than copied from one
row, e.g. `total_liabilities` on documents that only print financial/non-financial
sub-liabilities separately).

Two intentional annotation choices are visible as benign validator warnings, not defects:
- OFSS's `total_assets` sets `source_has_no_explicit_label=true` while still recording the
  literal (but ambiguous) label text `"TOTAL"` — the label exists, it's just shared with an
  unrelated second row on the same page, which is what the flag is documenting.
- Reliance's `revenue` records carry a `candidates` entry even though `status=valid`, used
  deliberately to document a plausible-but-wrong decoy line ("Value of Sales & Services
  (Revenue)") alongside the correct one, for future reference.

## Known failure classes represented

See `manifest.json`'s `known_failure_classes_represented` for the full list. Several were
independently reconfirmed from source across multiple documents (e.g.
`cross_statement_type_scope_inconsistency` — the production pipeline selecting a standalone
page for one statement type and a consolidated page for another within the same document
run — confirmed on Reliance, LICHSGFIN, and JioFin).

## Provenance

Every record traces back to a specific source page, with verbatim evidence text and, where
useful, `cross_reference_pages` corroborating the value from an independent part of the same
document (e.g. a segment note restating the same total). `labeled_by` is `"human"` on every
record, per the no-leakage rule.
