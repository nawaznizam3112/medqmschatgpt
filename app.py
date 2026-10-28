from __future__ import annotations

import streamlit as st

from qms.documents import (
    DocumentError,
    DocumentStatus,
    create_document,
    create_revision,
    get_document,
    list_documents,
    record_review,
    transition_document,
)
from qms.storage import init_db

st.set_page_config(page_title="MedDevice QMS Lab", page_icon="🩺", layout="wide")
init_db()

st.title("🩺 MedDevice QMS Lab")
st.caption("Learning prototype • Synthetic data only • Not a validated production eQMS")

page = st.sidebar.radio(
    "Workspace",
    ["Overview", "Document Control", "Learning Lab", "Architecture & Limitations"],
)

if page == "Overview":
    docs = list_documents()
    c1, c2, c3 = st.columns(3)
    c1.metric("Controlled documents", len(docs))
    c2.metric("Effective", sum(d["status"] == DocumentStatus.EFFECTIVE.value for d in docs))
    c3.metric("In review", sum(d["status"] == DocumentStatus.IN_REVIEW.value for d in docs))
    st.subheader("Document register")
    st.dataframe(docs, use_container_width=True, hide_index=True)
    st.info("Next module: nonconformance and CAPA, with containment, investigation, action, and effectiveness gates.")

elif page == "Document Control":
    st.subheader("Controlled Document Lifecycle")
    tab_create, tab_view, tab_action = st.tabs(["Create document", "View register", "Workflow actions"])

    with tab_create:
        with st.form("create_document"):
            doc_id = st.text_input("Document ID", placeholder="SOP-QA-001")
            title = st.text_input("Document title", placeholder="Control of documented information")
            doc_type = st.selectbox("Document type", ["SOP", "Work Instruction", "Form", "Policy", "Template"])
            owner = st.text_input("Document owner", value="Quality Engineer")
            reason = st.text_area("Purpose / reason for creation")
            submitted = st.form_submit_button("Create draft", type="primary")
            if submitted:
                try:
                    record = create_document(doc_id, title, doc_type, owner, reason)
                    st.success(f"Created draft {record['document_id']} revision {record['revision']}.")
                    st.rerun()
                except DocumentError as exc:
                    st.error(str(exc))

    with tab_view:
        docs = list_documents()
        if docs:
            st.dataframe(docs, use_container_width=True, hide_index=True)
            selected = st.selectbox("Inspect document", [d["document_id"] for d in docs])
            record = get_document(selected)
            st.json(record)
        else:
            st.info("No documents yet. Create a draft in the first tab.")

    with tab_action:
        docs = list_documents()
        if not docs:
            st.info("Create a document first.")
        else:
            selected = st.selectbox("Document to update", [d["document_id"] for d in docs], key="action_doc")
            record = get_document(selected)
            st.write(f"**Current state:** {record['status']} · **Revision:** {record['revision']}")
            action = st.selectbox(
                "Action",
                ["Submit for review", "Record review", "Approve", "Make effective", "Obsolete", "Create next revision"],
            )
            actor = st.text_input("Actor (demo attribution only)", value="Quality Engineer")
            note = st.text_area("Decision / change rationale")
            reviewer_role = st.selectbox("Review role (for review action)", ["Quality", "Engineering", "Regulatory"])
            decision = st.selectbox("Review decision", ["Accepted", "Changes required"])
            if st.button("Apply workflow action", type="primary"):
                try:
                    if action == "Submit for review":
                        transition_document(selected, DocumentStatus.IN_REVIEW, actor, note)
                    elif action == "Record review":
                        record_review(selected, actor, reviewer_role, decision, note)
                    elif action == "Approve":
                        transition_document(selected, DocumentStatus.APPROVED, actor, note)
                    elif action == "Make effective":
                        transition_document(selected, DocumentStatus.EFFECTIVE, actor, note)
                    elif action == "Obsolete":
                        transition_document(selected, DocumentStatus.OBSOLETE, actor, note)
                    else:
                        new_reason = note or "Revision initiated from UI"
                        new_rev = create_revision(selected, actor, new_reason)
                        st.success(f"Created revision {new_rev['revision']} as a draft.")
                    st.success("Action recorded.")
                    st.rerun()
                except DocumentError as exc:
                    st.error(str(exc))
            st.caption("Prototype attribution is not authentication or an electronic signature. Do not use this workflow for real controlled records.")

elif page == "Learning Lab":
    st.subheader("Exercise: create and release a controlled SOP")
    st.markdown("""
    **Scenario:** Your team needs a new procedure for control of incoming inspection records.

    1. Create `SOP-QA-001` as a draft.
    2. Submit it for review and record a Quality review as **Accepted**.
    3. Approve it and make it effective.
    4. Create a revision with a documented reason.
    5. Confirm the previous revision remains visible in the revision history.

    **Learning point:** a status label alone is not enough. A robust workflow must enforce prerequisites and retain evidence of decisions.
    """)

elif page == "Architecture & Limitations":
    st.subheader("Architecture")
    st.markdown("""
    - `app.py`: Streamlit presentation layer.
    - `qms/documents.py`: document lifecycle rules and validation.
    - `qms/storage.py`: SQLite persistence and audit-event storage.
    - `tests/`: automated business-rule tests.
    - `.github/workflows/tests.yml`: CI checks for pull requests and pushes.

    **Not production-ready:** there is no real authentication, role enforcement, electronic signature, tamper-resistant audit trail,
    validated backup/restore, deployment hardening, or formal intended-use validation. The actor field is demo attribution only.
    Use synthetic data. Do not store patient information or real product release records.
    """)
