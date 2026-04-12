"""Permission-aware document access resolution."""
import uuid
import logging
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User, UserGroup
from app.models.document import Document, DocumentAccessRule

logger = logging.getLogger(__name__)


async def get_accessible_document_ids(db: AsyncSession, user: User) -> Optional[List[str]]:
    """
    Returns the list of document IDs the user is allowed to access.
    Returns None if the user is an admin (access to everything).
    Returns an empty list if the user has no accessible documents.
    """
    if user.role == "admin":
        return None  # None = no filter = all documents

    # Get user's group IDs
    group_result = await db.execute(
        select(UserGroup.group_id).where(UserGroup.user_id == user.id)
    )
    group_ids = [str(row[0]) for row in group_result.all()]

    # Get documents accessible to this user
    # Rule types: 'all' | 'user' | 'group'
    result = await db.execute(
        select(DocumentAccessRule.document_id, Document.is_deleted)
        .join(Document, Document.id == DocumentAccessRule.document_id)
        .where(Document.is_deleted == False)  # noqa: E712
    )
    all_rules = result.all()

    accessible: set[str] = set()
    for rule_row in all_rules:
        # Need to load the actual rule to check subject_type
        pass

    # Simpler: fetch all rules and filter in Python
    rules_result = await db.execute(
        select(DocumentAccessRule)
        .join(Document, Document.id == DocumentAccessRule.document_id)
        .where(Document.is_deleted == False)  # noqa: E712
    )
    rules = rules_result.scalars().all()

    for rule in rules:
        if rule.subject_type == "all":
            accessible.add(str(rule.document_id))
        elif rule.subject_type == "user" and rule.subject_id == user.id:
            accessible.add(str(rule.document_id))
        elif rule.subject_type == "group" and str(rule.subject_id) in group_ids:
            accessible.add(str(rule.document_id))

    return list(accessible)
