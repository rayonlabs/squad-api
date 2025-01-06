"""
ORM definitions/methods for custom tools.
"""

import ast
from fastapi import HTTPException, status
from sqlalchemy.sql import func
from sqlalchemy import (
    Column,
    String,
    DateTime,
    Boolean,
    UniqueConstraint,
)
from sqlalchemy.orm import validates
from squad.database import Base, generate_uuid
from squad.tool.validator import CodeValidator


class Tool(Base):
    __tablename__ = "tools"
    tool_id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    code = Column(String, nullable=False)
    public = Column(Boolean, default=False)
    user_id = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "name", name="unique_user_tools"),)

    @validates("code")
    def validate_code(self, _, code):
        """
        Minimally validates user-provided tool code.
        """
        try:
            tree = ast.parse(code)
            if not isinstance(tree.body[0], ast.FunctionDef):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Code must define exactly one function.",
                )
            validator = CodeValidator(allowed_functions)
            validator.visit(tree)
            if validator.function_name is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Code must define exactly one function.",
                )
            try:
                compile(code, "<string>", "exec")
            except SyntaxError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Code syntax error: {str(e)}",
                )
            if validator.errors:
                raise HTTPException(
                    status=status.HTTP_400_BAD_REQUEST,
                    detail="Validation errors encountered: {validator.errors}",
                )
        except SyntaxError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Code syntax error: {str(e)}",
            )
        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(
                status=status.HTTP_400_BAD_REQUEST,
                detail=f"Unexpected validation error: {e}",
            )
        self.code = code
