# Polygon Migration Tool — Architecture & Functionality Documentation

## Overview
The **Polygon Migration Tool** is a full-stack Django application designed to streamline the migration of competitive programming problems from Codeforces' [Polygon platform](https://polygon.codeforces.com/) into an internal PostgreSQL database and cloud object storage.

This document serves as an architectural and technical guide for engineers taking over and maintaining this codebase. It traces each user interaction from the browser through the Django backend, database layer, cache layer, and external integrations.

---

## 1. System Architecture & Component Diagram

```
+-------------------------------------------------------------------------------------------------+
|                                            BROWSER / CLIENT                                     |
|     +-------------------------+                           +-------------------------------+     |
|     |  Login View (/users/)   |                           | Problem Migration Dashboard   |     |
|     |  (email & password)     |                           | (Fetch, DB Migrate, Storage)  |     |
|     +------------+------------+                           +---------------+---------------+     |
+------------------|--------------------------------------------------------|---------------------+
                   | POST /users/login/                                     | POST / (problem_id, action flags)
                   v                                                        v
+------------------+--------------------------------------------------------+---------------------+
|                                            DJANGO BACKEND                                       |
|                                                                                                 |
|   +-----------------------+     +------------------------+      +---------------------------+   |
|   | users.backends        |     | problems.views (index) |      | problems.storage          |   |
|   | EmailBackend          |     | Controller & Pipelines |      | BaseStorageProvider       |   |
|   +-----------+-----------+     +-----------+------------+      +-------------+-------------+   |
|               |                             |                                 |                 |
|               |                             +----------------+                | (S3 / Azure)    |
|               |                             |                |                |                 |
|               v                             v                v                v                 |
|   +-----------------------+     +-------------------+   +---------------------------+           |
|   | users.models.User     |     | problems.models   |   | problems.polygon_api      |           |
|   | Custom Auth Model     |     | Problem, Tests    |   | PolygonAPI Client         |           |
|   +-----------+-----------+     +---------+---------+   +-------------+-------------+           |
+---------------|---------------------------|---------------------------|-------------------------+
                |                           |                           |
                v                           v                           |
+---------------+---------------------------+-------+                   |
|                   POSTGRESQL DATABASE             |                   |
|  - users_user (staff auth & profiles)             |                   |
|  - problems_problem (statements, limits, checker) |                   |
|  - problems_sampletestcase (display samples)      |                   |
|  - problems_problemtestcase (truncated DB cases)  |                   |
|  - problems_problemtag (tag associations)         |                   |
+---------------------------------------------------+                   |
                                                                        |
                                         +------------------------------+---------------------------+
                                         |                                                          |
                                         v                                                          v
                      +--------------------------------------+           +-------------------------------------+
                      |           REDIS CACHE (6379)         |           |        EXTERNAL SERVICES            |
                      |  - Key: polygon_migration_test_cases |           | 1. Polygon API                      |
                      |    _{polygon_id}_count               |           |    - SHA-512 signed requests        |
                      |  - Key: polygon_migration_test_cases |           |    - Statements, tests, packages    |
                      |    _{polygon_id}_test_{idx}          |           | 2. Cloud Storage (S3 / Supabase)    |
                      |  - TTL: 30 minutes                   |           |    - test_cases/{id}/{num}          |
                      +--------------------------------------+           |    - test_cases/{id}/{num}.a        |
                                                                         +-------------------------------------+
```

---

## 2. End-to-End User Flows

### Flow 1: User Login
Authenticates staff members to grant access to the problem migration dashboard.

* **Trigger / User Action**:
  - The user navigates to `/users/login/`, inputs their registered email address and password into the login form, and clicks the **"Sign In"** button.
* **Frontend Payload**:
  - `POST /users/login/`
  - Payload: `csrfmiddlewaretoken=<token>`, `email=<email>`, `password=<password>`.
* **Backend Processing Order**:
  1. Routed via `PolygonMigration/urls.py` $\rightarrow$ `users/urls.py` to `users.views.login_view`.
  2. The form validates email format and password presence.
  3. `django.contrib.auth.authenticate(request, username=email, password=password)` is invoked.
  4. Authentication delegates to `users.backends.EmailBackend.authenticate()`, which queries `users.models.User` by `email__iexact=username` and verifies the password hash via Argon2 / PBKDF2 (`check_password`).
  5. If authenticated, `django.contrib.auth.login(request, user)` writes user session metadata into `django_session` and sets the `sessionid` HTTP-only cookie.
  6. The `index` view enforces `@user_passes_test(lambda u: u.is_authenticated and u.is_staff)`, verifying that the authenticated user possesses `is_staff=True`.
* **External APIs**: None.
* **Database Reads / Writes**:
  - Read: `users_user` table queried by email.
  - Write: `django_session` updated with session key; user `last_login` timestamp updated.
* **UI Feedback**:
  - On success: HTTP 302 redirect to `/` (the migration dashboard).
  - On failure: Login template re-renders with `Invalid email or password` error message.

---

### Flow 2: Fetching a Problem from Polygon
Retrieves statements, limits, solutions, and test cases from Polygon and displays them for review.

* **Trigger / User Action**:
  - On `/`, the user enters a numeric Polygon Problem ID (e.g., `631727`) into the input field and clicks **"Fetch Problem"**.
* **Frontend Payload**:
  - `POST /`
  - Payload: `csrfmiddlewaretoken=<token>`, `problem_id=631727`.
* **Backend Processing Order**:
  1. Handled by `problems.views.index`. Extracts `polygon_id = request.POST.get('problem_id')`.
  2. Instantiates `problems.polygon_api.PolygonAPI`.
  3. Checks if the problem already exists in PostgreSQL (`Problem.objects.filter(polygon_id=polygon_id).first()`).
  4. Calls `api.get_problem_info(polygon_id)` $\rightarrow$ calls Polygon method `problem.info`.
  5. Attempts to download the package via `api.download_and_extract_package(polygon_id)`:
     - Calls Polygon `problem.packages` to find the latest package ID.
     - Downloads the package archive, unzips to a temporary folder, and parses `problem.html` via `parse_problem_html()` (using `lxml.html`).
     - *Graceful Fallback*: If no package has been compiled on Polygon, it falls back to `api.get_statements(polygon_id)` (`problem.statements` API endpoint) to extract title, legend, input format, output format, and notes.
  6. Fetches test cases:
     - Checks Redis cache (`api.get_test_cases_from_redis(polygon_id)`).
     - If cache miss: queries Polygon `problem.tests` for test metadata, then iterates sequentially through each test calling `problem.testInput` and `problem.testAnswer`.
     - Caches test cases in Redis via `api.store_test_cases_in_redis(polygon_id, all_test_cases, expiry_hours=0.5)`.
  7. Fetches solutions via Polygon `problem.solutions` and identifies the main solution (`tag == 'MA'`).
* **External APIs Called**:
  - `https://polygon.codeforces.com/api/problem.info`
  - `https://polygon.codeforces.com/api/problem.packages`
  - `https://polygon.codeforces.com/api/problem.statements` (fallback)
  - `https://polygon.codeforces.com/api/problem.tests`
  - `https://polygon.codeforces.com/api/problem.testInput` & `problem.testAnswer`
  - `https://polygon.codeforces.com/api/problem.solutions`
  - `https://polygon.codeforces.com/api/problem.viewSolution`
* **Database & Storage Reads / Writes**:
  - DB: `SELECT` on `problems_problem` to check if problem was previously imported.
  - Redis: Writes `polygon_migration_test_cases_{id}_count` and `polygon_migration_test_cases_{id}_test_{idx}` with a 30-minute TTL.
* **UI Feedback**:
  - Page renders problem title, legend, constraints, memory and time limits, reference solution code preview, and collapsible previews of all regular and sample test cases.

---

### Flow 3: Migrating Problem to Database
Persists the problem statement, limits, tags, difficulty, and sample test cases into PostgreSQL.

* **Trigger / User Action**:
  - User reviews fetched data, selects a difficulty (`Easy`, `Medium`, `Hard`), selects/adds tags (e.g., `math`, `arrays`), and clicks **"Create/Update problem in Database"**.
* **Frontend Payload**:
  - `POST /`
  - Payload: `problem_id=631727`, `difficulty=easy`, `migrate_to_db=1`, `tags=['math', 'arrays']`.
* **Backend Processing Order**:
  1. `views.index` detects `migrate_to_db = request.POST.get('migrate_to_db')`.
  2. Validates that `difficulty` is provided; raises an exception if missing.
  3. Generates slug: `slug = slugify(title)`.
  4. Queries database: `Problem.objects.filter(Q(polygon_id=polygon_id)).first()`.
  5. **Insert or Update**:
     - If existing: Updates all fields (`title`, `slug`, `difficulty`, `problem_statement`, `input_format`, `output_format`, `time_limit`, `memory_limit`, `checker_type`, `notes`) and calls `.save()`.
     - If new: Invokes `Problem.objects.create(...)`.
  6. **Tag Association**:
     - Iterates over `selected_tags`. For each tag, finds or creates `ProblemTag.objects.get_or_create(tag_name=tag)`.
     - Clears existing tags on the problem and attaches the new tag set (`problem_obj.extra_tags.set(tags_to_set)`).
  7. **Sample Test Case Synchronization**:
     - Filters `test_cases` where `is_sample=True`.
     - Queries `SampleTestCase.objects.filter(problem=problem_obj).order_by('order')`.
     - For each sample, updates existing records by order or creates new `SampleTestCase` objects.
* **External APIs**: None (uses cached or already-fetched Polygon problem info).
* **Database Reads / Writes**:
  - Read/Write `problems_problem`: Insert new record or update existing row.
  - Read/Write `problems_problemtag` and `problems_problem_extra_tags` (many-to-many join table).
  - Read/Write `problems_sampletestcase`: Writes sample inputs and outputs for statement display.
* **UI Feedback**:
  - Green banner displayed: `Problem 'Array Sum' created/updated in database successfully.`
  - Migration action buttons for Test Cases and Cloud Storage become active.

---

### Flow 4: Migrating Test Cases to Database
Persists all test cases into the relational database (`ProblemTestCase` table).

* **Trigger / User Action**:
  - User clicks **"Migrate Test Cases to DB"**.
* **Frontend Payload**:
  - `POST /`
  - Payload: `problem_id=631727`, `migrate_test_cases_to_db=1`.
* **Backend Processing Order**:
  1. `views.index` verifies `Problem.objects.filter(polygon_id=polygon_id).first()` exists. If not, returns error: `"Please migrate the problem to the database first."`.
  2. Retrieves test cases from Redis cache (`api.get_test_cases_from_redis(polygon_id)`). If cache expired, re-fetches from Polygon and repopulates Redis.
  3. Queries existing records: `ProblemTestCase.objects.filter(problem=problem_obj).order_by('order')`.
  4. Iterates through each test case:
     - Extracts `input_data` and `output_data`.
     - **Truncation Policy**: Truncates input and output to 260 characters (`truncated_input = input_data[:260]`, `truncated_output = output_data[:260]`).
     - Updates existing `ProblemTestCase` record at index `idx` or creates a new `ProblemTestCase(problem=problem_obj, is_sample=..., input=truncated_input, output=truncated_output, order=order)`.
* **External APIs**: Polygon API only if Redis cache expired.
* **Database Reads / Writes**:
  - Read: `problems_problem`, `problems_problemtestcase`.
  - Write: Inserts/Updates rows in `problems_problemtestcase` table.
* **UI Feedback**:
  - Success banner: `"Test cases description migrated to database Successfully."`

---

### Flow 5: Uploading Test Cases to Cloud Storage
Uploads full, raw test-case inputs and outputs to cloud object storage.

* **Trigger / User Action**:
  - User clicks **"Migrate Test Cases to Cloud Storage"**.
* **Frontend Payload**:
  - `POST /`
  - Payload: `problem_id=631727`, `migrate_to_azure=1`.
* **Backend Processing Order**:
  1. `views.index` validates problem presence in database.
  2. Resolves database problem ID: `db_problem_id = problem_obj.id`.
  3. Calls `api.delete_problem_test_case_cache(db_problem_id)`.
  4. Calls `api.migrate_to_storage(polygon_id, db_problem_id=db_problem_id)`.
  5. `migrate_to_storage` retrieves test cases from Redis (or Polygon fallback).
  6. Obtains configured storage provider via `problems.storage.get_storage_provider()`.
  7. Cleans up existing storage blobs via `provider.delete_test_cases(db_problem_id)`.
  8. For each test case `(idx, test)`:
     - Calls `provider.upload_test_case(db_problem_id, idx, input_data, output_data)`.
     - Inputs are saved to: `test_cases/{db_problem_id}/{test_number}`.
     - Outputs are saved to: `test_cases/{db_problem_id}/{test_number}.a`.
* **External APIs**:
  - Cloud Object Storage API (AWS S3 / Supabase S3 / Azure Blob Storage).
  - Calls `PutObject` / `upload_blob` for each input and output.
* **Database & Storage Reads / Writes**:
  - DB: Read `problems_problem`.
  - Storage: $2 \times N$ objects created in bucket/container (`N` inputs, `N` outputs).
* **UI Feedback**:
  - Success banner: `"<N> test cases migrated to Cloud Storage successfully."`

---

## 3. External Integrations Architecture

### 3.1 Polygon API Integration

#### Authentication Protocol
Polygon API requests require SHA-512 digital signatures. The application implements this in `PolygonAPI._generate_api_sig()` (`problems/polygon_api.py`):
1. Parameters include `apiKey` and current UNIX timestamp `time`.
2. Parameters are sorted lexicographically by key.
3. A 6-character random alphanumeric nonce is generated (`rand_prefix`).
4. A hash string is constructed:
   $$\text{to\_hash} = \text{rand\_prefix} + "/" + \text{method\_name} + "?" + \text{urlencode}(\text{sorted\_params}) + "\#" + \text{api\_secret}$$
5. The signature is computed using SHA-512:
   $$\text{apiSig} = \text{rand\_prefix} + \text{SHA512}(\text{to\_hash})$$
6. `apiKey`, `time`, and `apiSig` are sent as POST form parameters to `https://polygon.codeforces.com/api/{method_name}`.

#### Polygon Endpoints Used
| Endpoint | Parameters | Purpose |
|---|---|---|
| `problem.info` | `problemId` | Fetches time limit, memory limit, interactive mode |
| `problem.packages` | `problemId` | Lists compiled problem packages |
| `problem.package` | `problemId, packageId, type` | Downloads binary package ZIP containing `problem.html` |
| `problem.statements` | `problemId` | Fetches raw statements, input/output format, and notes |
| `problem.tests` | `problemId, testset` | Lists test cases and `useInStatements` (sample) flags |
| `problem.testInput` | `problemId, testset, testIndex` | Downloads exact plain text input for a test case |
| `problem.testAnswer` | `problemId, testset, testIndex` | Downloads exact reference answer for a test case |
| `problem.solutions` | `problemId` | Lists problem solutions and flags (`MA` = Main) |
| `problem.viewSolution` | `problemId, name` | Retrieves source code of reference solution |
| `problem.checker` | `problemId` | Retrieves checker type (e.g. `std::ncmp.cpp`) |

---

### 3.2 Cloud Storage Architecture (`problems/storage/`)

To fulfill requirement 1.2, storage logic is decoupled behind the `BaseStorageProvider` abstract interface.

```
problems/storage/
├── __init__.py        # Exports get_storage_provider and BaseStorageProvider
├── base.py            # Abstract Base Class defining storage contract
├── s3.py              # S3-compatible provider (Supabase, AWS S3, Cloudflare R2)
├── azure.py           # Azure Blob Storage provider (backward compatibility)
└── factory.py         # Factory instantiating provider based on STORAGE_PROVIDER
```

#### Object Hierarchy & Naming Convention
All storage providers adhere strictly to the required directory hierarchy:
```
{bucket_or_container}/
└── test_cases/
    └── {problem_id}/
        ├── 1        # Test 1 Input file
        ├── 1.a      # Test 1 Output file
        ├── 2        # Test 2 Input file
        ├── 2.a      # Test 2 Output file
        ├── ...
        ├── 13       # Test 13 Input file
        └── 13.a     # Test 13 Output file
```

#### Provider Interface Contract
```python
class BaseStorageProvider(ABC):
    @abstractmethod
    def upload_test_case(self, db_problem_id: int | str, test_number: int, input_data: str, output_data: str) -> bool:
        """Uploads input to test_cases/{id}/{number} and output to test_cases/{id}/{number}.a"""
        pass

    @abstractmethod
    def delete_test_cases(self, db_problem_id: int | str) -> bool:
        """Deletes all blobs under test_cases/{id}/ prefix"""
        pass

    @abstractmethod
    def test_connection(self) -> bool:
        """Verifies credentials and bucket accessibility"""
        pass
```

#### Provider Configuration via Environment Variables
- `STORAGE_PROVIDER`: `s3` or `azure` (defaults to `s3`).
- For **S3 / Supabase**:
  - `AWS_ACCESS_KEY_ID`: S3 access key ID.
  - `AWS_SECRET_ACCESS_KEY`: S3 secret access key.
  - `AWS_S3_ENDPOINT_URL`: S3 endpoint URL (e.g., Supabase S3 storage endpoint).
  - `AWS_S3_BUCKET_NAME`: Bucket name (e.g., `test-cases`).
  - `AWS_REGION`: Target AWS region (default `us-east-1`).
- For **Azure**:
  - `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_CONTAINER_NAME`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_USERNAME`, `AZURE_PASSWORD`.

