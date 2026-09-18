# Contribution Statement

**Team:** Research Assistant Team
**Topic:** Topic 4 — Async Research Assistant
**Repository:** https://github.com/ruslan-sadigov/research-assistant-SWE-Final-Project
**Final tag:** `v1.0-final`
**Submission date:** [2026-09-18]

---

## How to fill this in

This is the single piece of evidence we use to assess **individual contribution** within the team. Rules:

1. Every member writes their own three subsections (Owned, Co-owned, Reviewed).
2. **Be specific.** "Worked on the backend" is not acceptable; "implemented `src/services/ai_service.py` and `src/concurrency/pipeline.py`, owned PRs #4, #7, #11" is.
3. The committed-percentages must add to 100% and approximately match `git shortlog -sn` on the `main` branch.
4. All three members must sign at the bottom. Unsigned submissions are returned ungraded.

If one member contributed less than 10% without a documented reason (illness, emergency), the team loses 5 points automatically per the rubric.

---

## Member A — Nihat Ismayilzade (`@NihatIsmayilzade`)

**Owned (sole author of these files / PRs):**
- `STUDENT_README.md`

**Co-owned (paired or substantially edited):**
- `src/services/ai_service.py`
- `tests/test_services.py`


**Approximate share of commits:** _[34]_%

---

## Member B — Polad Ibrahimli (`@Polad-Ibrahimli`)

**Owned:**
- `src/researcher/core/researcher.py` (validate_question/render_result/Researcher class)
- `tests/test_researcher.py`

**Co-owned:**
- `src/researcher/cli.py`
- `tests/test_cli.py`

**Approximate share of commits:** _[14]_%

---

## Member C — Samur Eyyubov (`@eyyubovsamur240-afk`)

**Owned (sole author of these files / PRs):**
- `src/researcher/services/cache.py` (cache keys, query normalisation, TTL expiry)
- `src/researcher/storage/cache_store.py` (SQLite and in-memory stores)
- `tests/test_cache.py`, `tests/test_cache_store.py`
- `tests/test_cache_store_contract.py`, `tests/test_cache_integration.py`, `tests/test_cache_verification.py`
- `tests/cache_verification.py` (offline cache hit-rate harness)
- `docs/caching.md`, `docs/cache-verification.md`, `docs/architecture.md`
- `docs/figures/architecture.tex` and the exported `architecture.pdf` / `architecture.png`
- `artefacts/demo/README.md`
- PRs: #14, #15

**Co-owned:**
- `src/researcher/storage/cache_store.py` and `tests/test_cache_store.py` — I wrote both; Ruslan hardened the transactions, schema checks and cancellation handling

**Reviewed:**
- PRs: none

**Approximate share of commits:** 15%

## AI tool disclosure (also in §10 of the report)

We used AI coding assistants as follows. Each item lists the module, the assistant, and what the team did with the output.

| Module / file | Assistant | What we did with it |
|---|---|---|
| _[e.g. `src/services/retry.py`]_ | _[Cursor]_ | _[Drafted initial backoff logic; team rewrote the jitter and retry-on-429 branch after observing rate-limit behavior in dev.]_ |
| _[e.g. `tests/test_pipeline.py`]_ | _[Claude]_ | _[Suggested test cases; team reviewed each, kept 4 of 6, hand-wrote 2 more.]_ |
| `docs/figures/architecture.*`, `docs/architecture.md`, `docs/cache-verification.md`, `tests/cache_verification.py`, `tests/test_cache_*.py` | Claude | Drafted the figure, the cache verification harness, the cache tests and the two cache documents; I reviewed each file, ran the full suite and the harness locally, and corrected the facts against the code before committing. |

We affirm that we **can defend every line of code** in this repository during the oral defense. "The AI wrote it" is not an answer we will use.

---

## Signatures

By signing below, we affirm that:
- The contributions described above are accurate.
- The commit percentages reflect actual work, not artificially split commits.
- Every line of code in the repository can be defended by at least one team member.
- AI assistant usage has been disclosed as described above.

| Member | Signature | Date |
|---|---|---|
| _[Full Name A]_ | ______Nihat Ismayilzade____________________ | __18.09.2026________ |
| _[Full Name B]_ | __________________________ | __________ |
| _[Full Name C]_ | __Samur Eyyubov________________________ | __18.09.2026________ |
