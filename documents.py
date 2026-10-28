from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from qms.storage import connect, init_db


class DocumentError(ValueError):
    """Raised when a document workflow rule is violated."""


class DocumentStatus(str, Enum):
    DRAFT = "Draft"
    IN_REVIEW = "In Review"
    APPROVED = "Approved"
    EFFECTIVE = "Effective"
    OBSOLETE = "Obsolete"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _required(value: str, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise DocumentError(f"{label} is required.")
    return cleaned


def _audit(conn, document_id: str, action: str, actor: str, details: str) -> None:
    conn.execute(
        """INSERT INTO audit_events(entity_type, entity_id, action, actor, details, created_at)
           VALUES ('document', ?, ?, ?, ?, ?)""",
        (document_id, action, actor, details, _now()),
    )


def create_document(document_id: str, title: str, document_type: str, owner: str, reason: str) -> dict:
    init_db()
    document_id = _required(document_id, "Document ID").upper()
    title = _required(title, "Title")
    document_type = _required(document_type, "Document type")
    owner = _required(owner, "Owner")
    reason = _required(reason, "Creation reason")
    actor = owner
    now = _now()
    with connect() as conn:
        if conn.execute("SELECT 1 FROM documents WHERE document_id = ?", (document_id,)).fetchone():
            raise DocumentError(f"Document ID '{document_id}' already exists.")
        conn.execute(
            """INSERT INTO documents
               (document_id, title, document_type, owner, revision, status, created_at, updated_at, change_reason)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (document_id, title, document_type, owner, DocumentStatus.DRAFT.value, now, now, reason),
        )
        _audit(conn, document_id, "created", actor, f"Revision 1 created: {reason}")
    return get_document(document_id)


def get_document(document_id: str) -> dict:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE document_id = ?", (document_id.upper(),)).fetchone()
        if row is None:
            raise DocumentError(f"Document '{document_id}' was not found.")
        reviews = conn.execute(
            "SELECT revision, reviewer, reviewer_role, decision, comment, created_at "
            "FROM document_reviews WHERE document_id = ? ORDER BY review_id",
            (document_id.upper(),),
        ).fetchall()
        audit = conn.execute(
            "SELECT action, actor, details, created_at FROM audit_events "
            "WHERE entity_type='document' AND entity_id=? ORDER BY event_id",
            (document_id.upper(),),
        ).fetchall()
    result = dict(row)
    result["reviews"] = [dict(r) for r in reviews]
    result["audit_events"] = [dict(r) for r in audit]
    return result


def list_documents() -> list[dict]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT document_id, title, document_type, owner, revision, status, updated_at, change_reason "
            "FROM documents ORDER BY document_id"
        ).fetchall()
    return [dict(r) for r in rows]


def record_review(document_id: str, reviewer: str, reviewer_role: str, decision: str, comment: str) -> None:
    document_id = _required(document_id, "Document ID").upper()
    reviewer = _required(reviewer, "Reviewer")
    reviewer_role = _required(reviewer_role, "Reviewer role")
    decision = _required(decision, "Review decision")
    comment = _required(comment, "Review comment")
    if decision not in {"Accepted", "Changes required"}:
        raise DocumentError("Review decision must be 'Accepted' or 'Changes required'.")
    with connect() as conn:
        row = conn.execute("SELECT revision, status FROM documents WHERE document_id=?", (document_id,)).fetchone()
        if row is None:
            raise DocumentError(f"Document '{document_id}' was not found.")
        if row["status"] != DocumentStatus.IN_REVIEW.value:
            raise DocumentError("A review can only be recorded while the document is In Review.")
        conn.execute(
            """INSERT INTO document_reviews
               (document_id, revision, reviewer, reviewer_role, decision, comment, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (document_id, row["revision"], reviewer, reviewer_role, decision, comment, _now()),
        )
        _audit(conn, document_id, "review_recorded", reviewer,
               f"Revision {row['revision']}; role={reviewer_role}; decision={decision}; comment={comment}")
        if decision == "Changes required":
            now = _now()
            conn.execute(
                "UPDATE documents SET status=?, updated_at=? WHERE document_id=?",
                (DocumentStatus.DRAFT.value, now, document_id),
            )
            _audit(conn, document_id, "returned_to_draft", reviewer, "Review requested changes.")


def transition_document(document_id: str, target: DocumentStatus, actor: str, note: str) -> None:
    document_id = _required(document_id, "Document ID").upper()
    actor = _required(actor, "Actor")
    note = _required(note, "Decision note")
    if not isinstance(target, DocumentStatus):
        raise DocumentError("Invalid target state.")
    allowed = {
        DocumentStatus.DRAFT: {DocumentStatus.IN_REVIEW},
        DocumentStatus.IN_REVIEW: {DocumentStatus.APPROVED, DocumentStatus.DRAFT},
        DocumentStatus.APPROVED: {DocumentStatus.EFFECTIVE, DocumentStatus.DRAFT},
        DocumentStatus.EFFECTIVE: {DocumentStatus.OBSOLETE},
        DocumentStatus.OBSOLETE: set(),
    }
    with connect() as conn:
        row = conn.execute("SELECT revision, status FROM documents WHERE document_id=?", (document_id,)).fetchone()
        if row is None:
            raise DocumentError(f"Document '{document_id}' was not found.")
        current = DocumentStatus(row["status"])
        if target not in allowed[current]:
            raise DocumentError(f"Transition from {current.value} to {target.value} is not allowed.")
        if target == DocumentStatus.APPROVED:
            accepted = conn.execute(
                "SELECT 1 FROM document_reviews WHERE document_id=? AND revision=? AND decision='Accepted' LIMIT 1",
                (document_id, row["revision"]),
            ).fetchone()
            if not accepted:
                raise DocumentError("Approval requires an accepted review for the current revision.")
        if target == DocumentStatus.EFFECTIVE:
            # A separate approval event is required in audit history for this revision.
            approved = any(
                e["action"] == "transition" and "->Approved" in e["details"]
                for e in conn.execute(
                    "SELECT action, details FROM audit_events WHERE entity_type='document' AND entity_id=?",
                    (document_id,),
                ).fetchall()
            )
            if current != DocumentStatus.APPROVED or not approved:
                raise DocumentError("Only an approved document can become effective.")
        now = _now()
        conn.execute(
            "UPDATE documents SET status=?, updated_at=? WHERE document_id=?",
            (target.value, now, document_id),
        )
        _audit(conn, document_id, "transition", actor, f"{current.value}->{target.value}; note={note}")


def create_revision(document_id: str, actor: str, reason: str) -> dict:
    document_id = _required(document_id, "Document ID").upper()
    actor = _required(actor, "Actor")
    reason = _required(reason, "Change reason")
    with connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
        if row is None:
            raise DocumentError(f"Document '{document_id}' was not found.")
        if row["status"] not in {DocumentStatus.EFFECTIVE.value, DocumentStatus.OBSOLETE.value}:
            raise DocumentError("A new revision can only be created from an Effective or Obsolete revision.")
        now = _now()
        # This starter stores the current revision number on the document record.
        # Full immutable revision snapshots are a planned next improvement.
        new_revision = row["revision"] + 1
        conn.execute(
            "UPDATE documents SET revision=?, status=?, updated_at=?, change_reason=? WHERE document_id=?",
            (new_revision, DocumentStatus.DRAFT.value, now, reason, document_id),
        )
        _audit(conn, document_id, "revision_created", actor, f"Revision {new_revision} created: {reason}")
    return get_document(document_id)
