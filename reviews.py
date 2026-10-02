"""Review system feature module."""

from bot import *
from bot import _github_headers

# =========================
# REVIEW DATABASE
# =========================

review_db = sqlite3.connect(
    REVIEW_DB_FILE,
    check_same_thread=False,
    timeout=15,
)
review_db.row_factory = sqlite3.Row
review_db.execute("PRAGMA busy_timeout = 15000")
review_db.execute("""
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER NOT NULL,
    reviewer_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    comment TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
review_db.execute('''
CREATE TABLE IF NOT EXISTS review_update_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    reviewer_id INTEGER NOT NULL,
    old_rating INTEGER NOT NULL,
    old_comment TEXT NOT NULL,
    new_rating INTEGER NOT NULL,
    new_comment TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    approval_message_id INTEGER,
    approval_channel_id INTEGER,
    reviewed_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP
)
''')
review_db.commit()

review_db_thread_lock = threading.RLock()


def _ensure_review_db_schema() -> None:
    """Make sure the review table exists, even after a GitHub DB restore."""
    with review_db_thread_lock:
        connection = sqlite3.connect(
            REVIEW_DB_FILE,
            check_same_thread=False,
            timeout=30,
        )
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER NOT NULL,
                    reviewer_id INTEGER NOT NULL,
                    rating INTEGER NOT NULL,
                    comment TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.execute('''
                CREATE TABLE IF NOT EXISTS review_update_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id INTEGER NOT NULL,
                    target_id INTEGER NOT NULL,
                    reviewer_id INTEGER NOT NULL,
                    old_rating INTEGER NOT NULL,
                    old_comment TEXT NOT NULL,
                    new_rating INTEGER NOT NULL,
                    new_comment TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    approval_message_id INTEGER,
                    approval_channel_id INTEGER,
                    reviewed_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TIMESTAMP
                )
            ''')
            connection.execute('''
                CREATE TABLE IF NOT EXISTS review_update_approval_context (
                    request_id INTEGER PRIMARY KEY,
                    approved INTEGER NOT NULL DEFAULT 1
                )
            ''')
            connection.execute('''
                CREATE TRIGGER IF NOT EXISTS prevent_unapproved_review_updates
                BEFORE UPDATE OF rating, comment ON reviews
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM review_update_approval_context c
                    JOIN review_update_requests r ON r.id = c.request_id
                    WHERE c.approved = 1
                      AND r.review_id = OLD.id
                      AND r.status = 'pending'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'Review updates require moderator approval.');
                END
            ''')
            connection.execute('''
                DELETE FROM reviews
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM reviews
                    GROUP BY target_id, reviewer_id
                )
            ''')
            connection.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_target_reviewer
                ON reviews(target_id, reviewer_id)
            ''')
            connection.commit()
        finally:
            connection.close()


def _reopen_review_db() -> None:
    global review_db
    try:
        review_db.close()
    except Exception:
        pass
    review_db = sqlite3.connect(
        REVIEW_DB_FILE,
        check_same_thread=False,
        timeout=15,
    )
    review_db.row_factory = sqlite3.Row
    review_db.execute("PRAGMA busy_timeout = 15000")
    review_db.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER NOT NULL,
            reviewer_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    review_db.execute('''
        CREATE TABLE IF NOT EXISTS review_update_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            reviewer_id INTEGER NOT NULL,
            old_rating INTEGER NOT NULL,
            old_comment TEXT NOT NULL,
            new_rating INTEGER NOT NULL,
            new_comment TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            approval_message_id INTEGER,
            approval_channel_id INTEGER,
            reviewed_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP
        )
    ''')
    review_db.execute('''
        CREATE TABLE IF NOT EXISTS review_update_approval_context (
            request_id INTEGER PRIMARY KEY,
            approved INTEGER NOT NULL DEFAULT 1
        )
    ''')
    review_db.execute('''
        CREATE TRIGGER IF NOT EXISTS prevent_unapproved_review_updates
        BEFORE UPDATE OF rating, comment ON reviews
        WHEN NOT EXISTS (
            SELECT 1
            FROM review_update_approval_context c
            JOIN review_update_requests r ON r.id = c.request_id
            WHERE c.approved = 1
              AND r.review_id = OLD.id
              AND r.status = 'pending'
        )
        BEGIN
            SELECT RAISE(ABORT, 'Review updates require moderator approval.');
        END
    ''')
    review_db.execute('''
        DELETE FROM reviews
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM reviews
            GROUP BY target_id, reviewer_id
        )
    ''')
    review_db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_target_reviewer
        ON reviews(target_id, reviewer_id)
    ''')
    review_db.commit()


def _sqlite_file_is_valid(path: Path) -> bool:
    """Return True when the file has a valid SQLite header and integrity check."""
    try:
        if not path.exists() or path.stat().st_size < 16:
            return False
        with path.open("rb") as file:
            if file.read(16) != b"SQLite format 3\\x00":
                return False
        connection = sqlite3.connect(path, timeout=10)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            return bool(result and result[0] == "ok")
        finally:
            connection.close()
    except Exception:
        return False


def _ensure_review_db_file_ready_sync() -> None:
    """Repair/restore the local review DB before a write if it is invalid."""
    global review_db

    if _sqlite_file_is_valid(REVIEW_DB_FILE):
        return

    with review_db_thread_lock:
        if _sqlite_file_is_valid(REVIEW_DB_FILE):
            return

        # First try the known-good GitHub database branch.
        if GITHUB_TOKEN:
            try:
                remote = _github_download_db(GITHUB_DB_PATH)
                if remote:
                    temp = REVIEW_DB_FILE.with_name("reviews.db.repair.tmp")
                    temp.write_bytes(remote)
                    if _sqlite_file_is_valid(temp):
                        try:
                            review_db.close()
                        except Exception:
                            pass
                        temp.replace(REVIEW_DB_FILE)
                        _reopen_review_db()
                        return
                    temp.unlink(missing_ok=True)
            except Exception:
                pass

        # Never leave an invalid file blocking new reviews.
        # Keep the bad file as a backup and create a clean database.
        if REVIEW_DB_FILE.exists():
            backup = REVIEW_DB_FILE.with_name("reviews.db.corrupt")
            try:
                backup.unlink(missing_ok=True)
            except Exception:
                pass
            REVIEW_DB_FILE.replace(backup)

        try:
            review_db.close()
        except Exception:
            pass
        _reopen_review_db()


class DuplicateReviewError(Exception):
    '''Raised when a reviewer already has an active review for a target.'''


class ReviewTransferConflict(Exception):
    """Raised when transferring reviews would create duplicate reviewer/target pairs."""


class PendingReviewUpdateError(Exception):
    '''Raised when a review already has a pending update request.'''


class ReviewStoreConflict(Exception):
    """Raised when another bot instance changed the shared GitHub store."""


GITHUB_REVIEW_STORE_PATH = "review_store.json"
review_store_thread_lock = threading.RLock()


def _new_review_store():
    return {
        "version": 1,
        "reviews": [],
        "review_update_requests": [],
    }


def _row_to_dict(row):
    return dict(row) if row is not None else None


def _seed_review_store_from_sqlite():
    store = _new_review_store()
    with review_db_thread_lock:
        connection = sqlite3.connect(REVIEW_DB_FILE, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            store["reviews"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT id, target_id, reviewer_id, rating, comment, created_at "
                    "FROM reviews ORDER BY id"
                ).fetchall()
            ]
            store["review_update_requests"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM review_update_requests ORDER BY id"
                ).fetchall()
            ]
        finally:
            connection.close()
    return store


def _github_review_store_request(method="GET", content=None, sha=None):
    url = _github_db_url(GITHUB_REVIEW_STORE_PATH)
    branch = urllib.parse.quote(GITHUB_DB_BRANCH, safe="")
    if method == "GET":
        url = f"{url}?ref={branch}"

    body = None
    headers = _github_headers()

    if method == "PUT":
        body = {
            "message": "Sync review store",
            "content": base64.b64encode(
                json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
            ).decode("ascii"),
            "branch": GITHUB_DB_BRANCH,
            "sha": sha,
        }
        headers = {**headers, "Content-Type": "application/json"}

    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        if error.code == 409:
            raise ReviewStoreConflict from error
        body_text = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub review store request failed ({error.code}): {body_text}"
        ) from error


def _github_read_review_store():
    if not GITHUB_TOKEN:
        return _new_review_store(), None

    payload = _github_review_store_request("GET")
    if payload is None:
        store = _seed_review_store_from_sqlite()
        return store, None

    encoded = payload.get("content")
    if not encoded:
        raise RuntimeError("GitHub review store is empty.")

    raw = base64.b64decode("".join(str(encoded).split())).decode("utf-8")
    store = json.loads(raw)

    if not isinstance(store, dict):
        raise RuntimeError("GitHub review store has invalid JSON.")

    store.setdefault("version", 1)
    store.setdefault("reviews", [])
    store.setdefault("review_update_requests", [])
    for request in store["review_update_requests"]:
        request.setdefault("update_reason", "")
    return store, payload.get("sha")


def _github_write_review_store(store, sha=None):
    payload = _github_review_store_request(
        "PUT",
        content=store,
        sha=sha,
    )
    if payload is None:
        raise RuntimeError("GitHub did not return the saved review store.")
    return payload.get("content", {}).get("sha")


def _github_update_review_store(mutator):
    """Atomically update the shared review store with conflict retries."""
    with review_store_thread_lock:
        last_error = None

        for _ in range(6):
            store, sha = _github_read_review_store()

            result = mutator(store)

            try:
                _github_write_review_store(store, sha)
                return result, store
            except ReviewStoreConflict as error:
                last_error = error
                time.sleep(0.5)

        raise RuntimeError(
            "The shared review store was changed by another bot instance. "
            "Please try again."
        ) from last_error


def _load_shared_review_store():
    with review_store_thread_lock:
        store, sha = _github_read_review_store()

        # Create the shared store when this is the first deployment using it.
        if sha is None and GITHUB_TOKEN:
            try:
                _github_write_review_store(store, None)
            except ReviewStoreConflict:
                store, _ = _github_read_review_store()

        return store


def _mirror_review_store_to_sqlite(store):
    """Keep reviews.db as a local/exported mirror of the shared store."""
    with review_db_thread_lock:
        connection = sqlite3.connect(REVIEW_DB_FILE, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM review_update_approval_context")
            connection.execute("DELETE FROM review_update_requests")
            connection.execute("DELETE FROM reviews")

            for review in store.get("reviews", []):
                connection.execute(
                    """
                    INSERT INTO reviews
                    (id, target_id, reviewer_id, rating, comment, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review["id"],
                        review["target_id"],
                        review["reviewer_id"],
                        review["rating"],
                        review["comment"],
                        review.get("created_at"),
                    ),
                )

            request_columns = [
                "id",
                "review_id",
                "target_id",
                "reviewer_id",
                "old_rating",
                "old_comment",
                "new_rating",
                "new_comment",
                "status",
                "approval_message_id",
                "approval_channel_id",
                "reviewed_by",
                "created_at",
                "reviewed_at",
            ]

            placeholders = ", ".join(["?"] * len(request_columns))
            column_sql = ", ".join(request_columns)

            for request in store.get("review_update_requests", []):
                connection.execute(
                    f"""
                    INSERT INTO review_update_requests
                    ({column_sql})
                    VALUES ({placeholders})
                    """,
                    [request.get(column) for column in request_columns],
                )

            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _ensure_shared_review_store():
    store = _load_shared_review_store()
    _mirror_review_store_to_sqlite(store)
    return store


def _generate_shared_id(existing_ids):
    """Return the next sequential ID in the shared review store.
    
    GitHub's compare-and-swap update is used by the shared-store writer, so
    concurrent Railway instances cannot both commit the same next ID.
    Deleted IDs are not reused.
    """
    numeric_ids = {int(value) for value in existing_ids}
    return max(numeric_ids, default=0) + 1


def add_review(target_id: int, reviewer_id: int, rating: int, comment: str):
    '''Insert one review per reviewer and target into the shared store.'''
    def mutator(store):
        for review in store["reviews"]:
            if (
                int(review["target_id"]) == int(target_id)
                and int(review["reviewer_id"]) == int(reviewer_id)
            ):
                raise DuplicateReviewError

        review_id = _generate_shared_id(
            {int(review["id"]) for review in store["reviews"]}
        )
        store["reviews"].append(
            {
                "id": review_id,
                "target_id": target_id,
                "reviewer_id": reviewer_id,
                "rating": rating,
                "comment": comment,
                "created_at": time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.gmtime(),
                ),
            }
        )
        return review_id

    review_id, store = _github_update_review_store(mutator)
    _mirror_review_store_to_sqlite(store)
    return review_id


def get_user_review(target_id: int, reviewer_id: int):
    store = _load_shared_review_store()
    matches = [
        review for review in store["reviews"]
        if int(review["target_id"]) == int(target_id)
        and int(review["reviewer_id"]) == int(reviewer_id)
    ]
    if not matches:
        return None
    return max(matches, key=lambda review: int(review["id"]))


def create_review_update_request(
    review_id,
    target_id,
    reviewer_id,
    old_rating,
    old_comment,
    new_rating,
    new_comment,
    update_reason,
):
    def mutator(store):
        for request in store["review_update_requests"]:
            if (
                int(request["review_id"]) == int(review_id)
                and request["status"] == "pending"
            ):
                raise PendingReviewUpdateError

        request_id = _generate_shared_id(
            {
                int(request["id"])
                for request in store["review_update_requests"]
            }
        )

        store["review_update_requests"].append(
            {
                "id": request_id,
                "review_id": review_id,
                "target_id": target_id,
                "reviewer_id": reviewer_id,
                "old_rating": old_rating,
                "old_comment": old_comment,
                "new_rating": new_rating,
                "new_comment": new_comment,
                "update_reason": update_reason,
                "status": "pending",
                "approval_message_id": None,
                "approval_channel_id": None,
                "reviewed_by": None,
                "created_at": time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.gmtime(),
                ),
                "reviewed_at": None,
            }
        )
        return request_id

    request_id, store = _github_update_review_store(mutator)
    _mirror_review_store_to_sqlite(store)
    return request_id


def get_review_update_request(request_id):
    store = _load_shared_review_store()
    for request in store["review_update_requests"]:
        if int(request["id"]) == int(request_id):
            return request
    return None


def get_pending_review_update_requests():
    store = _load_shared_review_store()
    return [
        request
        for request in store["review_update_requests"]
        if request["status"] == "pending"
        and request.get("approval_message_id") is not None
        and int(request.get("approval_channel_id") or 0)
        == REVIEW_UPDATE_APPROVAL_CHANNEL_ID
    ]


def set_review_update_message(request_id, channel_id, message_id):
    def mutator(store):
        for request in store["review_update_requests"]:
            if int(request["id"]) == int(request_id):
                request["approval_channel_id"] = channel_id
                request["approval_message_id"] = message_id
                return True
        raise RuntimeError("Review update request was not found.")

    _, store = _github_update_review_store(mutator)
    _mirror_review_store_to_sqlite(store)


def complete_review_update(request_id, moderator_id, approve):
    def mutator(store):
        request = next(
            (
                item for item in store["review_update_requests"]
                if int(item["id"]) == int(request_id)
                and item["status"] == "pending"
            ),
            None,
        )

        if request is None:
            return None, "already_handled"

        review = next(
            (
                item for item in store["reviews"]
                if int(item["id"]) == int(request["review_id"])
                and int(item["target_id"]) == int(request["target_id"])
                and int(item["reviewer_id"]) == int(request["reviewer_id"])
            ),
            None,
        )

        if review is None:
            request["status"] = "cancelled"
            request["reviewed_by"] = moderator_id
            request["reviewed_at"] = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.gmtime(),
            )
            return request, "cancelled"

        status = "approved" if approve else "rejected"

        if approve:
            review["rating"] = request["new_rating"]
            review["comment"] = request["new_comment"]

        request["status"] = status
        request["reviewed_by"] = moderator_id
        request["reviewed_at"] = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.gmtime(),
        )
        return request, status

    result, store = _github_update_review_store(mutator)
    _mirror_review_store_to_sqlite(store)
    return result


def get_reviews(target_id: int):
    store = _load_shared_review_store()
    return sorted(
        [
            review
            for review in store["reviews"]
            if int(review["target_id"]) == int(target_id)
        ],
        key=lambda review: int(review["id"]),
        reverse=True,
    )


def get_review(review_id: int):
    store = _load_shared_review_store()
    for review in store["reviews"]:
        if int(review["id"]) == int(review_id):
            return review
    return None


def transfer_reviews(old_target_id: int, new_target_id: int):
    """Move all reviews received by one account to another account."""
    old_target_id = int(old_target_id)
    new_target_id = int(new_target_id)

    if old_target_id == new_target_id:
        raise ValueError("The source and destination accounts must be different.")

    def mutator(store):
        source_reviews = [
            review for review in store["reviews"]
            if int(review["target_id"]) == old_target_id
        ]
        if not source_reviews:
            raise ValueError("The source account has no reviews to transfer.")

        source_reviewer_ids = {int(review["reviewer_id"]) for review in source_reviews}
        for review in store["reviews"]:
            if (
                int(review["target_id"]) == new_target_id
                and int(review["reviewer_id"]) in source_reviewer_ids
            ):
                raise ReviewTransferConflict

        moved_review_ids = {int(review["id"]) for review in source_reviews}
        for review in source_reviews:
            review["target_id"] = new_target_id

        for request in store.get("review_update_requests", []):
            if int(request.get("review_id", 0)) in moved_review_ids:
                request["target_id"] = new_target_id

        return len(source_reviews)

    count, store = _github_update_review_store(mutator)
    _mirror_review_store_to_sqlite(store)
    return count


def delete_review(review_id: int):
    def mutator(store):
        original_count = len(store["reviews"])
        store["reviews"] = [
            review
            for review in store["reviews"]
            if int(review["id"]) != int(review_id)
        ]
        if len(store["reviews"]) == original_count:
            raise RuntimeError("Review not found.")
        return True

    _, store = _github_update_review_store(mutator)
    _mirror_review_store_to_sqlite(store)


def _aggregate_target_stats(store, target_id):
    reviews = [
        review for review in store["reviews"]
        if int(review["target_id"]) == int(target_id)
    ]
    total = len(reviews)
    approved = sum(
        1 for review in reviews
        if int(review["rating"]) in (4, 5)
    )
    neutral = sum(
        1 for review in reviews
        if int(review["rating"]) == 3
    )
    negative = sum(
        1 for review in reviews
        if int(review["rating"]) in (1, 2)
    )
    average = (
        sum(int(review["rating"]) for review in reviews) / total
        if total else 0
    )
    return {
        "total": total,
        "average": average,
        "approved": approved,
        "neutral": neutral,
        "negative": negative,
        "approval": ((approved / total) * 100) if total else 0,
    }



def _review_db_snapshot() -> bytes:
    """Create a consistent SQLite snapshot while review writes are paused."""
    with review_db_thread_lock:
        temp = REVIEW_DB_FILE.with_name("reviews.db.sync.tmp")
        try:
            temp.unlink(missing_ok=True)
        except TypeError:
            if temp.exists():
                temp.unlink()

        source = sqlite3.connect(
            REVIEW_DB_FILE,
            check_same_thread=False,
            timeout=30,
        )
        snapshot = sqlite3.connect(temp, timeout=30)
        try:
            source.execute("PRAGMA busy_timeout = 30000")
            snapshot.execute("PRAGMA busy_timeout = 30000")
            source.backup(snapshot)
            snapshot.commit()
        finally:
            snapshot.close()
            source.close()

        try:
            return temp.read_bytes()
        finally:
            temp.unlink(missing_ok=True)

def _github_db_url(path: str) -> str:
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
    return f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{encoded}"


def _github_download_db(path: str) -> bytes | None:
    if not GITHUB_TOKEN:
        return None
    branch = urllib.parse.quote(GITHUB_DB_BRANCH, safe="")
    request = urllib.request.Request(
        f"{_github_db_url(path)}?ref={branch}",
        headers=_github_headers(),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub database download failed ({error.code}): {body}") from error
    encoded = payload.get("content")
    if not encoded:
        raise RuntimeError("GitHub returned an empty review database.")
    return base64.b64decode("".join(str(encoded).split()))


def _github_upload_db(path: str, content: bytes, message: str) -> None:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not configured.")
    branch = urllib.parse.quote(GITHUB_DB_BRANCH, safe="")
    url = _github_db_url(path)
    existing_sha = None
    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                f"{url}?ref={branch}",
                headers=_github_headers(),
                method="GET",
            ),
            timeout=30,
        ) as response:
            existing_sha = json.loads(response.read().decode("utf-8")).get("sha")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub database lookup failed ({error.code}): {body}") from error

    body = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": GITHUB_DB_BRANCH,
    }
    if existing_sha:
        body["sha"] = existing_sha

    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**_github_headers(), "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"GitHub database upload returned HTTP {response.status}.")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub database upload failed ({error.code}): {body}") from error


def _review_db_has_reviews() -> bool:
    try:
        return review_db.execute("SELECT 1 FROM reviews LIMIT 1").fetchone() is not None
    except Exception:
        return False


async def restore_review_db_from_github() -> bool:
    """Restore the latest review database before any review commands run."""
    if not GITHUB_TOKEN:
        return False
    if _review_db_has_reviews():
        return False

    try:
        remote = await asyncio.to_thread(_github_download_db, GITHUB_DB_PATH)
        if not remote:
            return False

        temp = REVIEW_DB_FILE.with_name("reviews.db.restore.tmp")
        temp.write_bytes(remote)
        try:
            review_db.close()
        except Exception:
            pass
        temp.replace(REVIEW_DB_FILE)
        _reopen_review_db()
        return True
    except Exception:
        return False

async def refresh_local_review_db_from_github() -> bool:
    """Load the latest shared review database before review operations."""
    if not GITHUB_TOKEN:
        return False

    temp = REVIEW_DB_FILE.with_name("reviews.db.latest.tmp")
    try:
        remote = await asyncio.to_thread(_github_download_db, GITHUB_DB_PATH)
        if not remote:
            return False

        await asyncio.to_thread(temp.write_bytes, remote)
        if not await asyncio.to_thread(_sqlite_file_is_valid, temp):
            return False

        with review_db_thread_lock:
            try:
                review_db.close()
            except Exception:
                pass
            temp.replace(REVIEW_DB_FILE)
            _reopen_review_db()

        return True
    except Exception as error:
        print(f"Review database refresh failed: {type(error).__name__}: {error}")
        return False
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except Exception:
                pass


async def sync_review_db_to_github() -> tuple[bool, str]:
    """Export the shared review store to reviews.db and then to GitHub."""
    if not GITHUB_TOKEN:
        return False, "GITHUB_TOKEN is not configured."

    try:
        store = await asyncio.to_thread(_load_shared_review_store)
        await asyncio.to_thread(_mirror_review_store_to_sqlite, store)
        snapshot = await asyncio.to_thread(_review_db_snapshot)
        await asyncio.to_thread(
            _github_upload_db,
            GITHUB_DB_PATH,
            snapshot,
            "Sync review database",
        )
        return True, ""
    except Exception as error:
        return False, str(error)


review_db_lock = asyncio.Lock()


async def sync_review_db_to_github_locked() -> tuple[bool, str]:
    """Serialize review database uploads so background syncs cannot overlap."""
    async with review_db_lock:
        return await sync_review_db_to_github()


# =========================

def review_user_is_blacklisted(user_id: int) -> bool:
    return user_id in review_blacklist


async def save_review_blacklist() -> bool:
    return await save_user_blacklist(REVIEW_BLACKLIST_FILE, review_blacklist)


async def review_blacklist_command_check(interaction: discord.Interaction) -> bool:
    if interaction.guild_id != MAIN_SERVER:
        raise app_commands.CheckFailure("This command can only be used in the main server.")

    if not isinstance(interaction.user, discord.Member):
        raise app_commands.CheckFailure("Could not verify your server roles.")

    if not any(
        role.id in {
            1397677852056354948,
            1518416402141417472,
            1306082718060384399,
        }
        for role in interaction.user.roles
    ):
        raise app_commands.CheckFailure(
            "You do not have permission to manage the review blacklist."
        )

    return True



review_blacklist_group = app_commands.Group(
    name="reviewblacklist",
    description="Manage the review user blacklist",
)

@review_blacklist_group.command(
    name="add",
    description="Blacklist a member from submitting or updating reviews.",
)
@app_commands.describe(member="The member to blacklist from reviews.")
@app_commands.check(review_blacklist_command_check)
async def review_blacklist_add_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    await interaction.response.defer()

    if member.id in review_blacklist:
        await interaction.followup.send(
            f"ℹ️ {member.mention} is already blacklisted from reviews.",
        )
        return

    review_blacklist.add(member.id)
    synced = await save_review_blacklist()
    await log_review_event(
        "Review Blacklist Updated",
        f"{interaction.user.mention} blacklisted {member.mention} from the review system.",
        fields=[
            ("Member", f"{member.mention} / {member.id}", True),
            ("Changed By", f"{interaction.user.mention} / {interaction.user.id}", True),
            ("GitHub Sync", "Success" if synced else "Failed", True),
        ],
        color=discord.Color.red(),
    )

    message = f"✅ {member.mention} can no longer submit or update reviews."
    await interaction.followup.send(message)

    if not synced:
        await interaction.followup.send(
            f"⚠️ GitHub sync failed: {github_blacklist_sync_error}",
            ephemeral=True,
        )


@review_blacklist_group.command(
    name="remove",
    description="Remove a member from the review blacklist.",
)
@app_commands.describe(member="The member to remove from the review blacklist.")
@app_commands.check(review_blacklist_command_check)
async def review_blacklist_remove_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    await interaction.response.defer()

    if member.id not in review_blacklist:
        await interaction.followup.send(
            f"ℹ️ {member.mention} is not currently blacklisted from reviews.",
        )
        return

    review_blacklist.remove(member.id)
    synced = await save_review_blacklist()
    await log_review_event(
        "Review Blacklist Updated",
        f"{interaction.user.mention} removed {member.mention} from the review blacklist.",
        fields=[
            ("Member", f"{member.mention} / {member.id}", True),
            ("Changed By", f"{interaction.user.mention} / {interaction.user.id}", True),
            ("GitHub Sync", "Success" if synced else "Failed", True),
        ],
        color=discord.Color.green(),
    )

    message = f"✅ {member.mention} can submit and update reviews again."
    await interaction.followup.send(message)

    if not synced:
        await interaction.followup.send(
            f"⚠️ GitHub sync failed: {github_blacklist_sync_error}",
            ephemeral=True,
        )


@review_blacklist_group.command(
    name="status",
    description="Check whether a member is blacklisted from reviews.",
)
@app_commands.describe(member="The member to check.")
@app_commands.check(review_blacklist_command_check)
async def review_blacklist_status_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    status = member.id in review_blacklist
    await interaction.response.send_message(
        f"ℹ️ {member.mention} is **{'blacklisted' if status else 'not blacklisted'}** from the review system.",
    )


def review_stars(rating: int) -> str:
    return "⭐" * rating + "☆" * (5 - rating)


def review_approval_emoji(rating: int) -> str:
    if rating in (4, 5):
        return "🟢"
    if rating == 3:
        return "🟠"
    return "🔴"


async def get_review_member_or_user(
    guild: discord.Guild,
    user_id: int
):
    member = guild.get_member(user_id)

    if member:
        return member

    try:
        return await bot.fetch_user(user_id)
    except:
        return None


# ============================================================
# DISCORD EMBED LIMITS
# ============================================================

# Anti-spam cooldown for reviews targeting the same member.
# After one person submits a review, other people must wait before submitting
# another review for that same target.
REVIEW_TARGET_COOLDOWN_SECONDS = 5 * 60
_review_target_cooldowns: dict[int, float] = {}


def get_review_target_cooldown_remaining(target_id: int) -> int:
    """Return remaining target cooldown seconds, or 0 when available."""
    now = time.monotonic()
    expires_at = _review_target_cooldowns.get(target_id, 0.0)
    remaining = expires_at - now

    if remaining <= 0:
        _review_target_cooldowns.pop(target_id, None)
        return 0

    return max(1, int(remaining + 0.999))


def start_review_target_cooldown(target_id: int) -> None:
    _review_target_cooldowns[target_id] = (
        time.monotonic() + REVIEW_TARGET_COOLDOWN_SECONDS
    )


EMBED_FIELD_VALUE_LIMIT = 1024


def clamp_embed_field_value(value, limit: int = EMBED_FIELD_VALUE_LIMIT) -> str:
    """Clamp text to Discord's embed field value limit.

    Discord rejects embed field values longer than 1024 characters, which is
    easy to hit because the review modal allows a 1000 character comment and
    the display wraps it in a mention plus a blockquote prefix. Discord counts
    UTF-16 code units rather than code points, so len() undercounts characters
    outside the basic multilingual plane; measuring and clipping the encoded
    form keeps astral characters from pushing a valid string over the limit.
    """
    text = "" if value is None else str(value)
    encoded = text.encode("utf-16-le")

    if len(encoded) <= limit * 2:
        return text

    # Reserve one UTF-16 code unit for the ellipsis.
    clipped = encoded[: (limit - 1) * 2].decode("utf-16-le", errors="ignore")

    return clipped.rstrip() + "…"


# ============================================================
# REVIEW LOGGING
# ============================================================

async def log_review_event(title, description, fields=None, color=None):
    """Send review activity to the configured log channel."""
    try:
        channel = bot.get_channel(REVIEW_LOG_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(REVIEW_LOG_CHANNEL_ID)

        embed = discord.Embed(
            title=title,
            description=description,
            color=color or discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )

        for name, value, inline in fields or []:
            embed.add_field(
                name=name,
                value=clamp_embed_field_value(value),
                inline=inline,
            )

        await channel.send(embed=embed)
    except Exception:
        # Logging must never break the review system.
        pass


# ============================================================
# REVIEW COMMENT MODAL
# ============================================================

class ReviewModal(discord.ui.Modal):

    def __init__(
        self,
        target: discord.Member,
        rating: int,
        reviewer_id: int
    ):
        super().__init__(
            title=f"Leave a {rating}-star review"
        )

        self.target = target
        self.rating = rating
        self.reviewer_id = reviewer_id

        self.comment = discord.ui.TextInput(
            label="Review",
            placeholder="Write your review...",
            style=discord.TextStyle.paragraph,
            required=True,
            min_length=1,
            max_length=1000
        )

        self.add_item(self.comment)

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        # The review flow belongs to the user who invoked /review.
        # This prevents someone else from submitting a review through another
        # person's open review prompt.
        if interaction.user.id != self.reviewer_id:
            await interaction.response.send_message(
                "❌ This review prompt belongs to another user. Use /review yourself.",
                ephemeral=True
            )
            return

        # Acknowledge the modal immediately so Discord does not time out.
        await interaction.response.defer(ephemeral=False)

        if review_user_is_blacklisted(interaction.user.id):
            await interaction.followup.send(
                "❌ You are blacklisted from using the review system.",
                ephemeral=False
            )
            return

        if interaction.user.id == self.target.id:
            await interaction.followup.send(
                "❌ You can't review yourself.",
                ephemeral=False
            )
            return

        if self.target.bot:
            await interaction.followup.send(
                "❌ Discord bot accounts cannot receive reviews.",
                ephemeral=False
            )
            return

        try:
            async with review_db_lock:
                await refresh_local_review_db_from_github()

                cooldown_remaining = get_review_target_cooldown_remaining(
                    self.target.id
                )
                if cooldown_remaining:
                    minutes, seconds = divmod(cooldown_remaining, 60)
                    if minutes:
                        wait_text = f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
                    else:
                        wait_text = f"{seconds}s"

                    await interaction.followup.send(
                        f"⏳ **Review cooldown:** another review for {self.target.mention} "
                        f"was submitted recently. Please wait **{wait_text}** before "
                        "submitting another review for this member.",
                        ephemeral=False,
                    )
                    return

                review_id = add_review(
                    target_id=self.target.id,
                    reviewer_id=interaction.user.id,
                    rating=self.rating,
                    comment=self.comment.value
                )

                # Start the cooldown only after the review was successfully
                # added, so failed/duplicate submissions do not block others.
                start_review_target_cooldown(self.target.id)

                sync_success, sync_error = await sync_review_db_to_github()
        except DuplicateReviewError:
            await interaction.followup.send(
                '❌ You already reviewed this user. Use `/updatereview` to change your vote.',
                ephemeral=False,
            )
            return
        except Exception as error:
            # Return the real database error instead of hiding it behind the
            # generic Discord modal failure message. This also makes Railway
            # logs useful if the database itself is unavailable.
            print(
                f"Review database error: {type(error).__name__}: {error}"
            )
            await interaction.followup.send(
                "❌ I couldn't save that review to the database.\n"
                f"`{type(error).__name__}: {error}`",
                ephemeral=True
            )
            return

        await interaction.followup.send(
            f"✅ Your review for {self.target.mention} was added.\n"
            f"**Rating:** {review_stars(self.rating)}\n"
            f"**Review ID:** `{review_id}`",
            ephemeral=False
        )
        if not sync_success:
            print(f"Review database save after /review failed: {sync_error}")

        await log_review_event(
            "Review Added",
            f"{interaction.user.mention} submitted a review for {self.target.mention}.",
            fields=[
                ("Review ID", f"Review #{review_id}", True),
                ("Rating", review_stars(self.rating), True),
                ("Reviewer", f"{interaction.user.mention} / {interaction.user.id}", False),
                ("Target", f"{self.target.mention} / {self.target.id}", False),
                ("Comment", str(self.comment.value)[:1024], False),
            ],
            color=discord.Color.green(),
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception
    ):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Something went wrong while saving the review. Please try again.",
                    ephemeral=False
                )
            else:
                await interaction.response.send_message(
                    "❌ Something went wrong while saving the review. Please try again.",
                    ephemeral=False
                )
        except Exception:
            pass


# ============================================================
# STAR DROPDOWN
# ============================================================

class StarSelect(discord.ui.Select):

    def __init__(self, target: discord.Member, reviewer_id: int):

        self.target = target
        self.reviewer_id = reviewer_id

        options = [
            discord.SelectOption(
                label="1 Star",
                value="1",
                emoji="⭐"
            ),
            discord.SelectOption(
                label="2 Stars",
                value="2",
                emoji="⭐"
            ),
            discord.SelectOption(
                label="3 Stars",
                value="3",
                emoji="⭐"
            ),
            discord.SelectOption(
                label="4 Stars",
                value="4",
                emoji="⭐"
            ),
            discord.SelectOption(
                label="5 Stars",
                value="5",
                emoji="⭐"
            )
        ]

        super().__init__(
            placeholder="select a star rating...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(
        self,
        interaction: discord.Interaction
    ):

        # A second layer of protection in case this select is ever
        # used outside its parent View.
        if interaction.user.id != self.reviewer_id:
            await interaction.response.send_message(
                "❌ This review prompt belongs to another user.",
                ephemeral=True
            )
            return

        rating = int(self.values[0])

        await interaction.response.send_modal(
            ReviewModal(
                target=self.target,
                rating=rating,
                reviewer_id=self.reviewer_id
            )
        )


class StarView(discord.ui.View):

    def __init__(
        self,
        target: discord.Member,
        reviewer_id: int
    ):
        super().__init__(timeout=120)

        self.reviewer_id = reviewer_id

        self.add_item(
            StarSelect(target, reviewer_id)
        )

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:
        if interaction.user.id != self.reviewer_id:
            await interaction.response.send_message(
                "❌ This review prompt belongs to another user. Use /review yourself.",
                ephemeral=True
            )
            return False

        return True


def review_main_server_check(interaction: discord.Interaction) -> bool:
    """Allow the review system to operate only in the configured main server."""
    return interaction.guild_id == MAIN_SERVER


# ============================================================
# /review
# ============================================================

@tree.command(
    name="review",
    description="Leave a review for a member."
)
@app_commands.check(review_main_server_check)
@app_commands.describe(
    user="The member you want to review."
)
async def review(
    interaction: discord.Interaction,
    user: discord.Member
):

    if review_user_is_blacklisted(interaction.user.id):
        await interaction.response.send_message(
            "❌ You are blacklisted from using the review system.",
            ephemeral=False
        )
        return

    if user.id == interaction.user.id:
        await interaction.response.send_message(
            "❌ You can't review yourself.",
            ephemeral=False
        )
        return

    if user.bot:
        await interaction.response.send_message(
            "❌ Discord bot accounts cannot receive reviews.",
            ephemeral=False
        )
        return

    # /review creates a review only. Existing reviews must be changed through
    # /updatereview so the moderator approval workflow is always used.
    existing = get_user_review(user.id, interaction.user.id)
    if existing:
        await interaction.response.send_message(
            "❌ You already reviewed this user. Use /updatereview to request a change. "
            "Your existing review will stay unchanged until a moderator approves it.",
            ephemeral=False
        )
        return

    await interaction.response.send_message(
        "**select your star rating:**",
        view=StarView(
            target=user,
            reviewer_id=interaction.user.id,
        ),
        ephemeral=False
    )


# ============================================================
# REVIEW PAGINATION
# ============================================================

class ReviewPagination(discord.ui.View):

    def __init__(
        self,
        target: discord.Member,
        reviews: list
    ):
        super().__init__(timeout=180)

        self.target = target
        self.reviews = reviews
        self.page = 0

        self.per_page = 5

        self.previous.disabled = True

        if len(reviews) <= self.per_page:
            self.next.disabled = True

    def make_embed(self):

        start = self.page * self.per_page
        end = start + self.per_page

        page_reviews = self.reviews[start:end]

        # Keep this module independent from leaderboard.py.
        # Calculate the stats directly from the shared review store.
        stats = _aggregate_target_stats(
            _load_shared_review_store(),
            self.target.id,
        )
        embed = discord.Embed(
            title=f"reviews for {self.target.display_name}",
            color=discord.Color.dark_grey()
        )

        embed.set_thumbnail(
            url=self.target.display_avatar.url
        )

        embed.description = (
            f"🟢 **{stats['approved']}**  "
            f"🟠 **{stats['neutral']}**  "
            f"🔴 **{stats['negative']}**\n"
        )

        for review in page_reviews:

            reviewer = self.target.guild.get_member(
                review["reviewer_id"]
            )

            if reviewer:
                reviewer_name = reviewer.display_name
                reviewer_mention = reviewer.mention
            else:
                reviewer_name = "Unknown User"
                reviewer_mention = f"<@{review['reviewer_id']}>"

            embed.add_field(
                name=(
                    f"{review_approval_emoji(int(review['rating']))} "
                    f"{review_stars(review['rating'])} — "
                    f"by {reviewer_name} · ID {review['id']}"
                ),
                value=clamp_embed_field_value(
                    f"{reviewer_mention}\n"
                    f"> {review['comment']}"
                ),
                inline=False
            )

        total_pages = max(
            1,
            (len(self.reviews) + self.per_page - 1)
            // self.per_page
        )

        embed.set_footer(
            text=f"Page {self.page + 1}/{total_pages}"
        )

        return embed

    @discord.ui.button(
        emoji="◀",
        style=discord.ButtonStyle.secondary
    )
    async def previous(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if self.page > 0:
            self.page -= 1

        self.next.disabled = False
        self.previous.disabled = self.page == 0

        await interaction.response.edit_message(
            embed=self.make_embed(),
            view=self
        )

    @discord.ui.button(
        emoji="▶",
        style=discord.ButtonStyle.secondary
    )
    async def next(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        max_page = (
            len(self.reviews) - 1
        ) // self.per_page

        if self.page < max_page:
            self.page += 1

        self.previous.disabled = False
        self.next.disabled = self.page >= max_page

        await interaction.response.edit_message(
            embed=self.make_embed(),
            view=self
        )


# ============================================================
# REVIEW UPDATE / APPROVAL
# ============================================================

def review_update_embed(request, status=None, moderator_id=None):
    embed = discord.Embed(
        title=f'Review Update Request #{request["id"]}',
        description=f'<@{request["reviewer_id"]}> requested an update for <@{request["target_id"]}>.',
        color=discord.Color.orange() if status is None else (discord.Color.green() if status == 'approved' else discord.Color.red()),
    )
    embed.add_field(
        name='Current Vote',
        value=clamp_embed_field_value(
            f'{review_stars(request["old_rating"])}\n> {request["old_comment"]}'
        ),
        inline=False,
    )
    embed.add_field(
        name='Proposed Vote',
        value=clamp_embed_field_value(
            f'{review_stars(request["new_rating"])}\n> {request["new_comment"]}'
        ),
        inline=False,
    )
    embed.add_field(
        name='Reason for Update',
        value=clamp_embed_field_value(
            request.get("update_reason") or "No reason provided."
        ),
        inline=False,
    )
    if status:
        embed.add_field(
            name='Decision',
            value=f'{status.title()} by <@{moderator_id}>',
            inline=False,
        )
    embed.set_footer(text='Review update awaiting moderator approval. Use the buttons below.')
    return embed


async def post_review_update_request(request_id):
    request = get_review_update_request(request_id)
    if not request:
        raise RuntimeError('Review update request was not found.')
    channel = bot.get_channel(REVIEW_UPDATE_APPROVAL_CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(REVIEW_UPDATE_APPROVAL_CHANNEL_ID)
    approval_message = await channel.send(
        embed=review_update_embed(request),
        view=ReviewUpdateApprovalView(request_id),
    )
    set_review_update_message(request_id, REVIEW_UPDATE_APPROVAL_CHANNEL_ID, approval_message.id)
    return approval_message


class ReviewUpdateApprovalView(discord.ui.View):
    """Persistent Approve/Reject buttons for moderator review of vote updates."""
    def __init__(self, request_id: int, disabled: bool = False):
        super().__init__(timeout=None)
        self.request_id = request_id

        approve_button = discord.ui.Button(
            label='Approve',
            style=discord.ButtonStyle.success,
            custom_id=f'review_update_approve:{request_id}',
            disabled=disabled,
        )
        reject_button = discord.ui.Button(
            label='Reject',
            style=discord.ButtonStyle.danger,
            custom_id=f'review_update_reject:{request_id}',
            disabled=disabled,
        )
        approve_button.callback = self.approve_callback
        reject_button.callback = self.reject_callback
        self.add_item(approve_button)
        self.add_item(reject_button)

    async def approve_callback(self, interaction: discord.Interaction):
        await handle_review_update_decision(interaction, self.request_id, True)

    async def reject_callback(self, interaction: discord.Interaction):
        await handle_review_update_decision(interaction, self.request_id, False)


class UpdateReviewModal(discord.ui.Modal):
    def __init__(self, target, current_review):
        super().__init__(title='Update your vote')
        self.target = target
        self.current_review = current_review
        self.rating = discord.ui.TextInput(
            label='New rating (1-5)',
            placeholder='Enter 1, 2, 3, 4, or 5',
            required=True,
            max_length=1,
            default=str(current_review['rating']),
        )
        self.comment = discord.ui.TextInput(
            label='Review text',
            placeholder='Leave blank to keep your current review text.',
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
            default=current_review['comment'],
        )
        self.update_reason = discord.ui.TextInput(
            label='Reason for updating this review',
            placeholder='Explain why you are changing your review...',
            style=discord.TextStyle.paragraph,
            required=True,
            min_length=10,
            max_length=500,
        )
        self.add_item(self.rating)
        self.add_item(self.comment)
        self.add_item(self.update_reason)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        try:
            new_rating = int(self.rating.value.strip())
            if new_rating < 1 or new_rating > 5:
                raise ValueError
        except ValueError:
            await interaction.followup.send('❌ Rating must be a number from 1 to 5.', ephemeral=False)
            return

        new_comment = self.comment.value.strip() or self.current_review['comment']
        update_reason = self.update_reason.value.strip()

        if len(update_reason) < 10:
            await interaction.followup.send(
                '❌ You must provide at least 10 characters explaining why you are updating the review.',
                ephemeral=False,
            )
            return

        try:
            request_id = create_review_update_request(
                self.current_review['id'],
                self.target.id,
                interaction.user.id,
                self.current_review['rating'],
                self.current_review['comment'],
                new_rating,
                new_comment,
                update_reason,
            )
        except PendingReviewUpdateError:
            await interaction.followup.send('❌ You already have an update waiting for approval for this vote.', ephemeral=False)
            return
        except Exception as error:
            await interaction.followup.send(f'❌ I could not create the update request: `{error}`', ephemeral=True)
            return

        try:
            await post_review_update_request(request_id)
        except Exception:
            connection = sqlite3.connect(REVIEW_DB_FILE, timeout=30)
            try:
                connection.execute('DELETE FROM review_update_requests WHERE id = ? AND status = ?', (request_id, 'pending'))
                connection.commit()
            finally:
                connection.close()
            await interaction.followup.send('❌ I could not send the update to the approval channel. Check the bot permissions there.', ephemeral=False)
            return

        asyncio.create_task(sync_review_db_to_github_locked())
        await interaction.followup.send(
            f'✅ Your updated vote was sent for approval. Request ID: `{request_id}`',
            ephemeral=False,
        )


@tree.command(
    name='updatereview',
    description='Submit an updated vote for a member for moderator approval.'
)
@app_commands.check(review_main_server_check)
@app_commands.describe(user='The member whose review you want to update.')
async def updatereview(interaction: discord.Interaction, user: discord.Member):
    if review_user_is_blacklisted(interaction.user.id):
        await interaction.response.send_message(
            "❌ You are blacklisted from using the review system.",
            ephemeral=False,
        )
        return

    await refresh_local_review_db_from_github()
    if user.id == interaction.user.id:
        await interaction.response.send_message('❌ You cannot update a review for yourself.', ephemeral=False)
        return
    if user.bot:
        await interaction.response.send_message('❌ Discord bot accounts cannot receive reviews.', ephemeral=False)
        return
    current = get_user_review(user.id, interaction.user.id)
    if not current:
        await interaction.response.send_message('❌ You have not reviewed this user yet. Use `/review` first.', ephemeral=False)
        return
    await interaction.response.send_modal(UpdateReviewModal(user, current))


async def review_approval_allowed(interaction: discord.Interaction) -> bool:
    return (
        isinstance(interaction.user, discord.Member)
        and any(role.id in DELETE_REVIEW_ALLOWED_ROLE_IDS for role in interaction.user.roles)
    )


async def handle_review_update_decision(interaction: discord.Interaction, request_id: int, approve: bool):
    if interaction.channel_id != REVIEW_UPDATE_APPROVAL_CHANNEL_ID:
        await interaction.response.send_message(
            f'❌ Review updates must be approved or rejected in <#{REVIEW_UPDATE_APPROVAL_CHANNEL_ID}>.',
            ephemeral=False,
        )
        return
    if not await review_approval_allowed(interaction):
        await interaction.response.send_message('❌ You do not have permission to approve or reject review updates.', ephemeral=False)
        return
    await interaction.response.defer(ephemeral=False)
    request, status = await asyncio.to_thread(complete_review_update, request_id, interaction.user.id, approve)
    if request is None:
        await interaction.followup.send('❌ That review update has already been handled or does not exist.', ephemeral=False)
        return
    try:
        channel = bot.get_channel(request['approval_channel_id']) if request['approval_channel_id'] else None
        if channel is None and request['approval_channel_id']:
            channel = await bot.fetch_channel(request['approval_channel_id'])
        if channel is not None and request['approval_message_id']:
            message = await channel.fetch_message(request['approval_message_id'])
            await message.edit(
                embed=review_update_embed(request, status, interaction.user.id),
                view=ReviewUpdateApprovalView(request_id, disabled=True),
            )
    except Exception:
        pass
    asyncio.create_task(sync_review_db_to_github_locked())
    if status == 'approved':
        text = f'✅ Review update #{request_id} approved. The vote has been updated.'
    elif status == 'rejected':
        text = f'✅ Review update #{request_id} rejected. The original vote remains unchanged.'
    else:
        text = f'⚠️ Review update #{request_id} was cancelled because the original review no longer exists.'
    await interaction.followup.send(text, ephemeral=False)


async def register_pending_review_update_views():
    """Re-register persistent buttons after a bot restart."""
    for request in get_pending_review_update_requests():
        try:
            bot.add_view(
                ReviewUpdateApprovalView(request['id']),
                message_id=request['approval_message_id'],
            )
        except Exception:
            pass

# ============================================================
# /transferreviews
# ============================================================

def review_transfer_allowed(interaction: discord.Interaction, source_id: int) -> bool:
    if interaction.user.id == int(source_id):
        return True
    if not isinstance(interaction.user, discord.Member):
        return False
    return any(
        role.id in DELETE_REVIEW_ALLOWED_ROLE_IDS
        for role in interaction.user.roles
    )


@tree.command(
    name="transferreviews",
    description="Transfer all reviews received by one account to another account."
)
@app_commands.check(review_main_server_check)
@app_commands.describe(
    from_account="The account currently receiving the reviews.",
    to_account="The account that should receive the transferred reviews.",
)
async def transferreviews(
    interaction: discord.Interaction,
    from_account: discord.Member,
    to_account: discord.Member,
):
    if from_account.id == to_account.id:
        await interaction.response.send_message(
            "❌ The source and destination accounts must be different.",
            ephemeral=False,
        )
        return

    if not review_transfer_allowed(interaction, from_account.id):
        await interaction.response.send_message(
            "❌ Only the account receiving the reviews or authorized review staff can transfer them.",
            ephemeral=False,
        )
        return

    if from_account.bot:
        await interaction.response.send_message(
            "❌ Bot accounts cannot be review-transfer sources.",
            ephemeral=False,
        )
        return

    if to_account.bot:
        await interaction.response.send_message(
            "❌ Bot accounts cannot receive reviews.",
            ephemeral=False,
        )
        return

    await interaction.response.defer(ephemeral=False)

    try:
        count = transfer_reviews(from_account.id, to_account.id)
        await sync_review_db_to_github_locked()
    except ValueError as error:
        await interaction.followup.send(f"❌ {error}", ephemeral=False)
        return
    except ReviewTransferConflict:
        await interaction.followup.send(
            "❌ The transfer would create duplicate reviews because at least one reviewer "
            "has already reviewed the destination account. No reviews were moved.",
            ephemeral=False,
        )
        return
    except Exception as error:
        print(f"Review transfer error: {type(error).__name__}: {error}")
        await interaction.followup.send(
            "❌ I couldn't transfer those reviews.",
            ephemeral=False,
        )
        return

    await log_review_event(
        "Reviews Transferred",
        f"{interaction.user.mention} transferred reviews from {from_account.mention} to {to_account.mention}.",
        fields=[
            ("Transferred By", f"{interaction.user.mention} / {interaction.user.id}", False),
            ("From", f"{from_account.mention} / {from_account.id}", True),
            ("To", f"{to_account.mention} / {to_account.id}", True),
            ("Reviews Moved", str(count), True),
        ],
        color=discord.Color.blurple(),
    )

    await interaction.followup.send(
        f"✅ Transferred **{count}** review(s) from {from_account.mention} to {to_account.mention}. "
        "The existing review IDs and reviewer accounts were preserved.",
        ephemeral=False,
    )


# ============================================================
# /reviews
# ============================================================

@tree.command(
    name="reviews",
    description="View a member's reviews."
)
@app_commands.check(review_main_server_check)
@app_commands.describe(
    user="The member whose reviews you want to see."
)
async def reviews(
    interaction: discord.Interaction,
    user: discord.Member
):

    await refresh_local_review_db_from_github()
    review_list = get_reviews(user.id)

    if not review_list:
        await interaction.response.send_message(
            f"**{user.display_name}** has no reviews yet.",
            ephemeral=False
        )
        return

    view = ReviewPagination(
        target=user,
        reviews=review_list
    )

    await interaction.response.send_message(
        embed=view.make_embed(),
        view=view
    )




def deletereview_role_allowed(interaction: discord.Interaction) -> bool:
    """Allow /deletereview only to members with one of the configured roles."""
    if not isinstance(interaction.user, discord.Member):
        return False
    return any(
        role.id in DELETE_REVIEW_ALLOWED_ROLE_IDS
        for role in interaction.user.roles
    )


@tree.command(
    name="deletereview",
    description="Delete a review by ID."
)
@app_commands.check(review_main_server_check)
@app_commands.describe(
    review_id="The review ID to delete."
)
@app_commands.check(deletereview_role_allowed)
async def deletereview(
    interaction: discord.Interaction,
    review_id: int
):
    # Discord requires an initial response within ~3 seconds. The GitHub/database
    # refresh can take longer, so acknowledge the interaction immediately.
    await interaction.response.defer(ephemeral=False)

    await refresh_local_review_db_from_github()
    review = get_review(review_id)

    if not review:
        await interaction.followup.send(
            "❌ Review not found.",
            ephemeral=False
        )
        return

    delete_review(review_id)
    asyncio.create_task(sync_review_db_to_github_locked())

    await log_review_event(
        "Review Removed",
        f"{interaction.user.mention} removed review #{review_id}.",
        fields=[
            ("Review ID", f"Review #{review_id}", True),
            ("Removed By", f"{interaction.user.mention} / {interaction.user.id}", False),
            ("Reviewer", f"<@{review['reviewer_id']}> / {review['reviewer_id']}", False),
            ("Target", f"<@{review['target_id']}> / {review['target_id']}", False),
            ("Rating", review_stars(int(review["rating"])), True),
            ("Comment", str(review.get("comment") or "No comment")[:1024], False),
        ],
        color=discord.Color.red(),
    )

    await interaction.followup.send(
        f"✅ Review `{review_id}` deleted.",
        ephemeral=False
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@deletereview.error
async def deletereview_error(
    interaction: discord.Interaction,
    error
):

    if isinstance(error, app_commands.errors.CheckFailure):
        message = "❌ You do not have one of the required roles to use `/deletereview`."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=False)
        else:
            await interaction.response.send_message(message, ephemeral=False)
    else:
        raise error



async def review_db_auto_sync_worker():
    """Reliably sync the review database immediately and every 30 minutes."""
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            success, error = await sync_review_db_to_github_locked()
            if not success:
                print(f"Review database auto-save failed: {error}")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"Review database auto-save crashed: {type(error).__name__}: {error}")

        await asyncio.sleep(REVIEW_DB_SYNC_MINUTES * 60)


@tree.command(
    name="savedb",
    description="Immediately save the review database to GitHub."
)
async def savedb_command(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "❌ This command can only be used inside a server.",
            ephemeral=False,
        )
        return

    if REVIEW_DB_SAVE_ROLE_ID not in {role.id for role in interaction.user.roles}:
        await interaction.response.send_message(
            "❌ You do not have permission to use `/savedb`.",
            ephemeral=False,
        )
        return

    await interaction.response.defer(ephemeral=False)
    async with review_db_lock:
        success, error = await sync_review_db_to_github()

    if success:
        await interaction.followup.send(
            "✅ The review database was saved to GitHub successfully.",
            ephemeral=False,
        )
    else:
        await interaction.followup.send(
            f"❌ I could not save the review database to GitHub.\n`{error}`",
            ephemeral=True,
        )




# Register the review blacklist group from the module that owns it.
tree.add_command(review_blacklist_group)
