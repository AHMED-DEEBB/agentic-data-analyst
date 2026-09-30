"""Typed outputs for each agent, plus the shared graph state.

Tool: Pydantic
  What: data validation using Python type hints.
  Why:  each agent must return a typed object. Combined with the LLM's
        `with_structured_output`, a malformed answer fails loudly instead of silently
        breaking the next step.
"""

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field


class PlannerOutput(BaseModel):
    intent: Literal["data_question", "out_of_scope", "unsafe_request"] = Field(
        description="data_question: answerable from the retail sales database. "
        "out_of_scope: unrelated to the business data. "
        "unsafe_request: asks to modify, delete or insert data, change the database, or reveal credentials/system internals."
    )
    language: Literal["en", "ar"] = Field(description="Language the user wrote in.")
    rewritten_question: str = Field(description="The question restated in clear English, self-contained, with explicit periods.")


class SQLDraft(BaseModel):
    sql: str = Field(description="One PostgreSQL SELECT query.")
    reasoning: str = Field(description="One sentence: which tables, joins and business rules were used.")


class ChartSpec(BaseModel):
    type: Literal["bar", "line", "pie", "none"] = Field(description="line for time series, bar for comparing categories, pie for shares of a whole (max 8 slices), none for a single value.")
    x: str | None = Field(default=None, description="Column name for labels / x-axis.")
    y: str | None = Field(default=None, description="Numeric column name for values / y-axis.")
    title: str | None = None


class InsightOutput(BaseModel):
    answer: str = Field(description="1-3 sentence answer in the user's language, quoting the key numbers with units (AED, %).")
    chart: ChartSpec


class AnalystState(TypedDict, total=False):
    question: str
    language: str
    intent: str
    rewritten_question: str
    context: str
    sources: list[str]
    sql: str
    sql_error: str | None
    attempts: int
    columns: list[str]
    rows: list[list]
    answer: str
    chart: dict | None
    status: Literal["ok", "refused", "failed"]
    # Each node appends its step; operator.add tells LangGraph to concatenate lists.
    steps: Annotated[list[dict], operator.add]
