# Issues Analysis & Edge Cases

## Summary

| Type | Critical | High | Medium | Low | Total |
|------|----------|------|--------|-----|-------|
| Product Issues | 1 | 1 | 1 | 0 | 3 |
| Code Issues | 1 | 1 | 1 | 0 | 3 |

---

## Product Issues

> Product issues represent problems experienced from the user's perspective: broken functionality, data corruption, poor user experience, or operational failure during problem onboarding.

### [P1] Silent Data Loss & False Success on Upstream Polygon API Rate Limits (HTTP 429)

**Severity**: Critical

**Location**: `problems/polygon_api.py:385-412` and `problems/views.py:270-295`

**Description**:
When retrieving all test cases from Polygon, the application iterates sequentially over all test indices and issues separate HTTP POST requests for `problem.testInput` and `problem.testAnswer`. During this loop, individual fetch errors are wrapped in a generic `try...except` block:
```python
# problems/polygon_api.py
except Exception as e:
    logger.warning(f"Error fetching test case {test_index}: {e}")
```
Polygon enforces strict rate limiting. When issuing 20+ sequential requests without delays, Polygon responds with `HTTP 429: Too Many Requests`. The exception is caught, logged only to server logs, and the test case `input` and `output` default to empty strings.
Subsequently, during cloud storage migration (`views.py:233`), test cases with empty inputs/outputs are silently skipped:
```python
if input_data and output_data:
    storage_provider.upload_test_case(...)
```
The view then renders a bright green success banner to the user:
`"13 test cases migrated to Cloud Storage successfully."` (or `"Test cases migrated successfully"`). In reality, tests #6 through #13 failed to fetch and were silently omitted from the migration.

**Impact**:
- **Critical Data Loss**: Partial test suites are uploaded while the staff user believes the migration succeeded 100%.
- **Academic & Platform Integrity Compromise**: On an online judge platform, problems with missing test cases allow incorrect or brute-force student submissions to pass with Accepted (AC), breaking leaderboard and grading integrity.
- **Unaware Users**: Because the UI reports unqualified success, staff members have no reason to inspect server logs to discover the dropped test cases.

**Recommended Improvement**:
1. **Remove Silent Exception Swallowing**: `polygon_api.py` must track failed fetches explicitly. If any test case fails, mark the batch as incomplete.
2. **Exponential Backoff & Rate-Limit Handling**: Implement automated retries with exponential backoff (`time.sleep(1.5 * attempt)`) on HTTP 429 status codes.
3. **Accurate UI Reporting**: Instead of a generic success message, display an explicit reconciliation summary: `"Migrated 5 of 13 test cases. 8 test cases failed to download due to Polygon rate limits (Test indices: 6, 7, 8, 9, 10, 11, 12, 13)."`, with an actionable "Retry Failed Test Cases" option.

**Priority Rationale**:
This issue is ranked **#1 (Highest Priority)** because silent data loss combined with a misleading success confirmation is the most dangerous failure mode in any migration utility. If an application fails loudly, an engineer can intervene; if it fails silently while reporting success, corrupted data enters production unnoticed.

---

### [P2] Synchronous Blocking UI During Sequential Multi-Call Network Migrations

**Severity**: High

**Location**: `problems/views.py:123-584` and `problems/templates/problems/index.html:425-435`

**Description**:
All migration flows ("Fetch Problem", "Migrate to Database", "Migrate Test Cases to DB", and "Migrate to Cloud Storage") are executed synchronously within standard Django request-response HTTP cycles.
For a standard competitive programming problem with 50 test cases:
- Fetching requires $1 \text{ (info)} + 1 \text{ (packages)} + 1 \text{ (tests list)} + 50 \times 2 \text{ (inputs + answers)} = 103$ sequential external HTTP requests.
- Cloud storage migration requires another 100 HTTP PUT requests.
This synchronous execution takes between 45 and 150 seconds. During this entire window, the browser tab hangs with a static loading overlay (`showLoading('Migrating...')`).

**Impact**:
- **Gateway Timeouts**: Standard production reverse proxies (Nginx, AWS ALB, Cloudflare) enforce 30- to 60-second HTTP timeout thresholds. Long migrations reliably terminate with `HTTP 504 Gateway Timeout`.
- **Double Migration / Race Conditions**: Users facing an unresponsive UI frequently refresh the page or click buttons multiple times, triggering concurrent migration jobs for the same problem ID.
- **Poor User Experience**: The user receives zero granular progress feedback (no percentage, no current test case indicator).

**Recommended Improvement**:
1. **Asynchronous Background Processing**: Offload all external network operations (Polygon fetching and Cloud Storage uploading) to an asynchronous worker queue (Celery, Dramatiq, or Django-Q) backed by Redis.
2. **Job Status & Progress Bar**: The frontend POST immediately receives an HTTP 202 Accepted with a `task_id`. The client polls or listens to a Server-Sent Events (SSE) or WebSocket channel to display an animated progress bar: `"Uploading test 24 of 50 (48%)..."` with an elapsed time counter.

**Priority Rationale**:
This issue is ranked **#2** because synchronous blocking prevents the tool from fulfilling its core mission on real-world competitive programming problems. While P1 deals with data correctness, P2 dictates whether the application can complete at all on standard problem sets without timing out.

---

### [P3] Fragmented Non-Atomic Multi-Step Workflow with Destructive Overwrite Risk

**Severity**: Medium-High

**Location**: `problems/templates/problems/index.html:240-265` and `problems/views.py:190-245, 345-420, 510-565`

**Description**:
The problem migration workflow is fragmented into three disconnected, manual button clicks:
1. "Create/Update Problem in Database"
2. "Migrate Test Cases to DB"
3. "Migrate Test Cases to Cloud Storage"
There is no unified migration flow and no state machine validating the sequence. A user can migrate the problem statement, skip test cases, or upload to storage without saving test cases in PostgreSQL.
Furthermore, clicking "Migrate Test Cases to Cloud Storage" immediately invokes `storage_provider.delete_test_cases()` to purge all existing cloud storage blobs for that problem without displaying a confirmation dialog, warning prompt, or synchronization diff.

**Impact**:
- **Partial / Inconsistent Problem Records**: Problems frequently exist in PostgreSQL with 0 test cases because a user forgot to click the second or third button.
- **Accidental Production Overwrite**: Re-migrating an updated problem immediately wipes existing storage files without showing what changed between Polygon and Cloud Storage.

**Recommended Improvement**:
1. **Unified 1-Click Migration**: Provide a primary **"Complete Problem Migration"** button that runs metadata persistence, DB test case migration, and Cloud Storage upload in one automated sequence.
2. **Pre-Migration Sync Diff & Confirmation**: Before purging and uploading to Cloud Storage, present a diff modal summarizing the actions to be performed: `"This will upload 13 new test cases and overwrite existing storage files for Problem #1. Are you sure?"`.

**Priority Rationale**:
Ranked **#3** because human error caused by fragmented, multi-button user interfaces is a primary source of operational defects in content ingestion pipelines. Consolidating the workflow eliminates manual mistakes.

---

## Code Issues

> Code issues represent technical, architectural, maintainability, and code quality defects in the backend implementation.

### [C1] False Transaction Rollback Guarantees & Missing Database Transaction Boundaries

**Severity**: Critical

**Location**: `problems/views.py:567-575`

**Description**:
At line 567 of `problems/views.py`, the view encloses its operations in a broad `try...except Exception as e` block and constructs an error message claiming:
```python
# problems/views.py:569
context['error'] = f"Migration failed and all changes have been rolled back. Reason: {str(e)}"
```
However, **nowhere in `views.py` is `django.db.transaction.atomic` imported or used**, nor is `ATOMIC_REQUESTS=True` configured in `settings.py`.
In Django, every call to `.save()` or `.create()` commits immediately in autocommit mode. If an error occurs halfway through inserting 50 test cases (e.g. database disconnect, data validation error, or disk full on test #25), the problem record and tests #1 through #24 remain permanently committed in PostgreSQL.

**Impact**:
- **Data Integrity Violation (ACID Failure)**: The database is left in a corrupted, half-migrated state.
- **Misleading Developer & User Diagnostics**: The system explicitly tells the user that all changes were rolled back, when in reality partial data is committed. Troubleshooting becomes difficult because engineers trust the system's false assertion.

**Recommended Improvement**:
1. Wrap all database operations for a migration step inside an explicit transaction context:
   ```python
   from django.db import transaction

   try:
       with transaction.atomic():
           problem_obj.save()
           ProblemTestCase.objects.bulk_create(...)
   except Exception as e:
       # Now changes are genuinely rolled back by PostgreSQL
       context['error'] = f"Migration failed and database changes were aborted: {str(e)}"
   ```
2. For cloud storage uploads, track uploaded keys and trigger compensatory cleanup only if the enclosing database transaction fails.

**Priority Rationale**:
Ranked **#1 (Highest Priority)** among code issues because false claims of transactional integrity directly compromise the database. Software must never claim atomicity when it is operating in non-atomic autocommit mode.

---

### [C2] Monolithic "Fat View" Anti-Pattern & Tight Coupling Across Layers

**Severity**: High

**Location**: `problems/views.py:88-584` (`index` function spans ~500 lines)

**Description**:
The `index` view in `problems/views.py` violates the Single Responsibility Principle and Separation of Concerns. A single 500-line function contains:
1. Form extraction and HTTP request handling
2. Raw Polygon API HTTP calls and signature handling
3. ZIP archive downloading, file decompression, and filesystem cleanup
4. HTML scraping and XPath DOM queries using `lxml`
5. Direct PostgreSQL ORM operations on 4 distinct models
6. Low-level Redis connection management, key scanning, and serialization
7. Cloud storage provider initialization and blob streaming
8. C++ compiler orchestration for custom checkers

**Impact**:
- **Untestable Code**: It is nearly impossible to unit test individual business rules (e.g. tag association, sample test extraction, difficulty validation) without instantiating mock HTTP requests and mocking 5 different third-party subsystems simultaneously.
- **Extreme Fragility**: A minor bugfix or improvement in statement parsing risks breaking cloud storage uploading or Redis caching due to variable reuse across the 500-line function scope.
- **Slow Team Velocity**: New engineers onboarding to the codebase face high cognitive load trying to follow execution state across nested conditional branches within a single view.

**Recommended Improvement**:
Refactor the codebase into a clean **Domain Service Architecture**:
- `services/polygon_service.py`: Encapsulates all Polygon communication, retry logic, and package decompression.
- `services/problem_service.py`: Domain logic for problem statement parsing, tag management, and persistence.
- `services/testcase_service.py`: Business rules for sample filtering, database test-case truncation, and order tracking.
- `services/storage_service.py`: Coordinates cloud uploads via `BaseStorageProvider`.
The `index` view should merely parse incoming requests, invoke the appropriate domain service method, and return the template response.

**Priority Rationale**:
Ranked **#2** because architectural debt in the primary view impairs maintainability, prevents automated test coverage, and slows down all subsequent engineering iterations.

---

### [C3] Unapplied Database Migrations in Starter Code & Broken Schema Release Hygiene

**Severity**: High

**Location**: `problems/models.py:79-80` vs `problems/migrations/0001_initial.py`

**Description**:
In `problems/models.py`, two new model fields were added to the `Problem` class:
```python
notes = models.TextField(blank=True, null=True, help_text="Notes Section from Polygon")
genie_chat = models.BooleanField(default=False)
```
However, the author failed to generate or commit the corresponding Django migration file in `problems/migrations/`.
When an engineer sets up the project from scratch and executes `python manage.py migrate` as documented in `README.md`, Django applies only `0001_initial.py`. The resulting PostgreSQL table `problems_problem` does not contain the `notes` and `genie_chat` columns.
As soon as `views.py` attempts to query or save a problem (`Problem.objects.filter(...)` or `Problem.objects.create(...)`), PostgreSQL immediately crashes with:
`django.db.utils.ProgrammingError: column problems_problem.notes does not exist`

**Impact**:
- **Onboarding Blocker**: Completely halts local setup and execution for any engineer or automated deployment pipeline using the starter codebase.
- **Runtime Failure**: The entire problem migration interface crashes on any database interaction.

**Recommended Improvement**:
1. Commit the missing migration `0002_problem_genie_chat_problem_notes.py`.
2. Add a CI pre-commit hook or automated test step: `python manage.py makemigrations --check --dry-run`. This flags any unmigrated model modifications during pull request checks before code merges into master.

**Priority Rationale**:
Ranked **#3** because while easily corrected via `makemigrations`, unapplied migrations represent a fundamental breakdown in release hygiene that blocks setup and breaks continuous integration.

---

## Edge Case Analysis

### Q1: A Polygon problem has 0 sample test cases but 15 regular test cases. What happens when you migrate this problem?

**Methodology**: Tested during pipeline verification & verified via code audit (`problems/views.py:453-478` and `problems/templates/problems/index.html:200-240`).

**Detailed Analysis & Behavior**:
1. **Polygon API Fetch**:
   Polygon's `problem.tests` endpoint returns 15 test items, each with `'useInStatements': False`.
2. **Database Migration ("Create/Update problem in Database")**:
   At line 453 of `problems/views.py`:
   ```python
   sample_tests = [test for test in test_cases if test.get('is_sample', False)]
   ```
   `sample_tests` evaluates to an empty list `[]`. The loop `for idx, test in enumerate(sample_tests):` does not execute.
   - Zero records are created in `SampleTestCase`.
   - **Important Pre-Existing State Bug**: If the problem previously had sample test cases in the database (e.g. before the problem setter updated Polygon), existing `SampleTestCase` rows are **not deleted**; they remain orphaned in the database.
   - The `Problem` record is created/updated with `test_case_count = 15`.
3. **Test Case Migration to DB ("Migrate Test Cases to DB")**:
   `ProblemTestCase` records are created for all 15 test cases, each with `is_sample=False`.
4. **Cloud Storage Migration**:
   All 15 test cases are uploaded to storage as `test_cases/{id}/1` through `15` along with their `.a` output files.
5. **User Experience Impact**:
   In the web UI, the "Sample Test Cases" section either collapses or appears completely empty. For an online judge or platform user, the problem statement provides no input/output example, making it harder for competitors to understand the problem format.

---

### Q2: A problem is migrated with 20 test cases. Later, the problem setter removes 8 test cases on Polygon (now 12 remain). The problem is re-migrated. What happens?

**Methodology**: Tested and confirmed through code audit (`problems/views.py:530-563` and `problems/polygon_api.py:780-785`).

**Detailed Analysis & Behavior**:
When the problem is re-migrated with 12 test cases after previously having 20:
1. **Redis Cache Staleness Risk**:
   If the re-migration occurs within 30 minutes of the last fetch, `api.get_test_cases_from_redis()` returns the **cached 20 test cases** unless the Redis key was explicitly cleared!
2. **Database Test Cases (`ProblemTestCase`) — Orphaned Ghost Records**:
   Lines 530-563 of `views.py` handle test case database updates:
   ```python
   existing_test_cases = list(ProblemTestCase.objects.filter(problem=problem_obj).order_by('order'))
   # existing_test_cases has 20 items (indices 0 to 19)
   for idx, test in enumerate(test_cases): # test_cases has 12 items (indices 0 to 11)
       if idx < len(existing_test_cases):
           # Updates existing_test_cases[0] through existing_test_cases[11]
           ptc.save()
   ```
   **Critical Bug**: The code updates existing records `0` through `11`, but **never deletes records `12` through `19`**! There is no `ProblemTestCase.objects.filter(problem=problem_obj, order__gt=12).delete()`.
   As a result:
   - The database still contains **20 test cases**.
   - `problem.test_case_count` says `12`, but `ProblemTestCase.objects.filter(problem=problem).count()` returns `20`.
   - The 8 removed test cases linger forever as orphaned ghost records.
3. **Cloud Storage**:
   In `BaseStorageProvider`, `delete_test_cases(problem_id)` is invoked prior to uploading, which wipes all blobs under `test_cases/{id}/`. Then, the 12 active test cases are uploaded (`1` through `12`).
4. **State Desynchronization**:
   Cloud Storage correctly contains 12 test cases, but the PostgreSQL database contains 20 test cases. Any evaluator querying the database table will test against 8 invalid, deleted test cases.

---

### Q3: Two different Polygon problems have the exact same title: "Two Sum". You migrate the first one successfully. Then you try to migrate the second one. What happens?

**Methodology**: Tested and verified via code audit (`problems/models.py:58` and `problems/views.py:298, 380-398`).

**Detailed Analysis & Behavior**:
1. **First Problem Migration**:
   - Title: "Two Sum", Polygon ID: e.g. `1001`.
   - `views.py:298` computes `slug = slugify("Two Sum")` $\rightarrow$ `"two-sum"`.
   - Problem #1 is inserted into PostgreSQL with `slug="two-sum"` and `polygon_id="1001"`. The migration succeeds.
2. **Second Problem Migration**:
   - Title: "Two Sum", Polygon ID: e.g. `2002`.
   - `views.py:298` computes `slug = slugify("Two Sum")` $\rightarrow$ `"two-sum"`.
   - The code queries `Problem.objects.filter(Q(polygon_id="2002")).first()`, which returns `None` (it is a distinct problem on Polygon).
   - The execution falls into the `else` branch (`views.py:380`):
     ```python
     problem_obj = Problem.objects.create(
         polygon_id="2002",
         title="Two Sum",
         slug="two-sum",
         ...
     )
     ```
3. **Database Crash (`IntegrityError`)**:
   `Problem.slug` is defined with `unique=True` (`models.py:58: slug = models.SlugField(unique=True, max_length=255)`).
   PostgreSQL raises an integrity constraint violation:
   ```
   django.db.utils.IntegrityError: duplicate key value violates unique constraint "problems_problem_slug_key"
   DETAIL: Key (slug)=(two-sum) already exists.
   ```
4. **User Experience**:
   The exception is caught by the view's broad `try...except`, and the user is shown:
   `"Migration failed and all changes have been rolled back. Reason: duplicate key value violates unique constraint 'problems_problem_slug_key'"`
   The second problem **cannot be migrated at all** until its slug is disambiguated (e.g. by appending the Polygon ID or an auto-incrementing integer: `two-sum-2002`).

---

### Q4: When test cases are saved to the database via "Migrate Test Cases to DB", some data is intentionally discarded. What data is lost? Why might this cause problems?

**Methodology**: Code inspection (`problems/views.py:538-542` and `problems/models.py:125-145`).

**Detailed Analysis of Discarded Data**:
1. **String Content Beyond 260 Bytes (Truncation)**:
   In `problems/views.py:540-541`:
   ```python
   truncated_input = input_data[:260]
   truncated_output = output_data[:260]
   ```
   Any character after byte index 260 is permanently dropped when writing to `ProblemTestCase.input` and `ProblemTestCase.output`.
2. **Test Cases with Empty Input or Output**:
   In `problems/views.py:538`:
   ```python
   if input_data and output_data:
   ```
   If a test case has an empty input or an empty output (e.g., an empty file test case, a blank line test, or an interactive problem where standard output is blank), the entire test case is skipped and discarded from `ProblemTestCase`.
3. **Test Metadata & Generation Provenance**:
   Polygon maintains test-case generation scripts (e.g., `gen 100 50000 42`), manual/scripted flags, and test case descriptions. None of these generation parameters are saved in the database.

**Why This Causes Problems**:
1. **Truncated Test Case Execution**:
   In competitive programming, realistic test cases feature arrays of $10^5$ integers, large matrices, trees, or long strings that easily span hundreds of kilobytes. Truncating at 260 characters truncates inputs midway (e.g., reading half an integer or dropping the array body).
   If an internal evaluation service, unit test runner, or student code sandbox reads from the `ProblemTestCase` table instead of Cloud Storage, submissions will fail with premature `EOF (End of File)` errors, `Runtime Error (NZEC)`, or compare against truncated outputs, producing false verdicts.
2. **Missing Edge Cases**:
   Problems where an empty output represents a valid answer (e.g. "print all negative numbers" when no negatives exist) will have those valid test cases silently omitted from the database.
3. **Loss of Reproducibility**:
   Because test generator scripts and seeds are discarded, if test files are lost or corrupted, engineers cannot regenerate the original test cases without re-fetching from Polygon.

