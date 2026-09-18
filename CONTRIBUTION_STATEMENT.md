# Contribution Statement

**Team:** Research Assistant Team
**Topic:** Topic 4 — Async Research Assistant
**Repository:** https://github.com/ruslan-sadigov/research-assistant-SWE-Final-Project
**Final tag:** `v1.0-final`
**Submission date:** 2026-09-18

## Member A — Nihat Ismayilzade (`@NihatIsmayilzade`)

**Owned (sole author of these files / PRs):**
- `STUDENT_README.md`

**Co-owned (paired or substantially edited):**
- `src/services/ai_service.py`
- `tests/test_services.py`

**Reviewed (PRs reviewed and merged):**
- PRs: no recorded reviews or merges

**Approximate share of commits:** 14%

---

## Member B — Polad Ibrahimli (`@Polad-Ibrahimli`)

**Owned:**
- `src/researcher/core/researcher.py` (validate_question/render_result/Researcher class)
- `tests/test_researcher.py`

**Co-owned:**
- `src/researcher/cli.py`
- `tests/test_cli.py`

**Reviewed:**
- PRs: no recorded reviews or merges

**Approximate share of commits:** 17%

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
- PR #1 — self-merged; no recorded peer review

**Approximate share of commits:** 18%

---

## Member D — Ruslan Sadigov (`@ruslan-sadigov`)

**Owned:**
- Project setup, packaging, configuration, shared interfaces and research models: `pyproject.toml`, `src/researcher/config.py`, `src/researcher/interfaces.py`, and `src/researcher/models.py`
- Concurrent source orchestration and diagnostics: `src/researcher/concurrency/orchestrator.py` and `tests/test_orchestrator.py`
- AI-service reliability and arXiv rate limiting: `src/researcher/services/ai_service.py`, `src/researcher/services/arxiv_limit.py`, and their tests
- CI, Docker workflow, live-source benchmarks, and supporting documentation

**Co-owned:**
- `src/researcher/cli.py` and `tests/test_cli.py` — input validation, failure handling, and persistent source caching
- `src/researcher/storage/cache_store.py` and `tests/test_cache_store.py` — transaction, schema-check, and cancellation hardening

**Reviewed:**
- Reviewed: PR #13 — requested changes
- Merged: PRs #3, #4, #7, #9, #14, #15, #17

**Approximate share of commits:** 51%

---

## AI tool disclosure (also in §10 of the report)

We used AI coding assistants as follows. Each item lists the module, the assistant, and what the team did with the output.

| Module / file | Assistant | What we did with it |
|---|---|---|
| `docs/figures/architecture.*`, `docs/architecture.md`, `docs/cache-verification.md`, `tests/cache_verification.py`, `tests/test_cache_*.py` | Claude | Drafted the figure, the cache verification harness, the cache tests and the two cache documents; I reviewed each file, ran the full suite and the harness locally, and corrected the facts against the code before committing. |
| Researcher application code, tests, CI/Docker setup, and supporting documentation | OpenAI Codex | Assisted with implementation drafts, debugging, test design, CI/Docker configuration, and documentation. The responsible team members reviewed and adapted the output, ran the relevant checks, and verified the final behavior before committing. |

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
| Nihat Ismayilzade | **Nihat Ismayilzade** | **18.09.2026** |
| Polad Ibrahimli |  |  |
| Samur Eyyubov | **Samur Eyyubov** | **18.09.2026** |
| Ruslan Sadigov | **Ruslan Sadigov** | **18.09.2026** |
