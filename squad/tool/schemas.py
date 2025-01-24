"""
ORM definitions/methods for tools.
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
from sqlalchemy.dialects.postgresql import JSONB
from squad.database import Base, generate_uuid


class Tool(Base):
    __tablename__ = "tools"
    tool_id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    description = Column(String, nullable=False)
    template = Column(String, nullable=True)
    code = Column(String, nullable=True)
    tool_args = Column(JSONB, nullable=True)
    public = Column(Boolean, default=False)
    user_id = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "name", name="unique_tools"),)

    @validates("code")
    def validate_code(self, _, code):
        """
        Minimally validate user-provided tools.
          - Checks for syntax errors
          - Ensures exactly one tool class is defined
          - Verifies the tool class inherits from smolagents.Tool
        """
        if code is None:
            return code
        try:
            tree = ast.parse(code)
            tool_classes = [item for item in tree.body if isinstance(item, ast.ClassDef)]
            if len(tool_classes) != 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Code must define exactly one tool class!",
                )

            # Check class inheritance
            tool_class = tool_classes[0]
            has_tool_base = False
            for base in tool_class.bases:
                if isinstance(base, ast.Name) and base.id == "Tool":
                    has_tool_base = True
                    break
                elif isinstance(base, ast.Attribute):
                    if isinstance(base.value, ast.Name):
                        if base.value.id == "smolagents" and base.attr == "Tool":
                            has_tool_base = True
                            break
            if not has_tool_base:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Tool class must inherit from smolagents.Tool",
                )

            # Check if code compiles
            compile(code, "<string>", "exec")
        except SyntaxError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Code syntax error: {str(e)}",
            )
        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unexpected validation error: {e}",
            )
        return code
