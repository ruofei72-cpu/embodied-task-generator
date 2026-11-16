# Design notes

## A structured task, not just another sentence

An atomic task contains an action, a scene, base parameters, a structured goal,
and text templates. Generation changes selected parameters and renders all
output fields from the same state. The original instruction stays in the record
for traceability. Input task IDs and scene IDs are explicit; the tool does not
infer task semantics from free text.

## Configuration and sampling

Axis names identify the customer's variation dimensions; axis fields identify
the parameter each dimension changes. Scene catalogs limit allowable values.
Dimensions and quotas are independent. Seven dimensions do not imply that every
combination should be generated, nor that the quota must always be seven.

Single-axis candidates are enumerated round-robin across dimensions. Cross mode
enumerates pairs of dimensions. Each candidate uses a fresh deep copy of base
parameters, so previous changes cannot accumulate. Cross mode is not a pairwise
covering-array implementation. The candidate budget bounds enumeration; the
report states whether enumeration was truncated.

Candidates pass catalog, inventory and configured constraint checks before
selection. Selection greedily favors coverage of currently uncovered dimensions;
ties retain enumeration order. This makes reruns deterministic. Reordering a
configuration may change selection. The method is intentionally simple and does
not claim optimal diversity or semantic novelty.

## Complete groups and duplicates

Only complete atomic-task groups are exported. If the requested quota or required
axis coverage cannot be met, the group is rejected with diagnostics. Its candidate
fingerprints are not reserved, so failed groups cannot consume candidates needed
by subsequent tasks. Other valid groups in a partial batch can still export; the
CLI exits 1 so automation can detect partial failure.

Fingerprints hash the scene, action, parameter state and structured goal, excluding
the rendered instruction. Different wording of the same structured task is
therefore not counted as a new variant. This is exact structured deduplication,
not embedding-based similarity or arbitrary-language semantic equivalence.

## Two meanings of reset

1. Parameter reset is implemented by copying the original state for every
   candidate. It is part of the generation algorithm.
2. Physical reset is only described by `reset_instruction`, rendered for the
   selected variant. No object, robot or simulator is reset by this program.

## Validation and trust boundaries

- Templates accept simple named placeholders only; attribute/index access and
  format conversions are rejected. No `eval`, generated code or shell command is
  executed.
- JSON input rejects duplicate keys and NaN/Infinity. Labels use actual booleans.
- The canonical validator checks constraints, provenance fields, fingerprints,
  changed dimensions and complete groups. It does not prove that the prose
  correctly describes an action or that the robot can physically execute it.
- Spreadsheet exports escape formula prefixes. JSONL is the authoritative record;
  exported CSV/XLSX is a presentation/adapter view and is not accepted by the
  canonical validator.
- Candidates require human review. Configured rules express known requirements,
  not a physics engine, an affordance model or an automated safety certification.

## Optional LLM boundary

A future or external LLM can help convert source instructions into the documented
atomic-task schema, or suggest descriptions. The current release has no model
API adapter. Adding an LLM does not remove the need for parameter, quota and
constraint validation. Candidate generation and exports remain reproducible
without model access.

## Measuring useful improvements

For real usage, measure valid-group rate, per-axis coverage, rejection reasons,
duplicate rate and manual review effort on a held-out, labeled sample. Compare
against the original workflow with the same input and acceptance criteria. Do
not present synthetic example counts as customer adoption, production output,
robot success rates or time savings.
