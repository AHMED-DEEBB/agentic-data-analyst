"""LLM provider factory and tracing.

Tool: LangChain chat model integrations (langchain-groq, langchain-openai)
  What: a common interface over different LLM APIs, plus `with_structured_output`,
        which forces the model to return data matching a Pydantic schema.
  Why:  agents return typed objects (intent, SQL, chart spec) instead of free text we
        would have to parse. Provider is swappable: Groq (free), OpenAI, or Azure OpenAI.

Tool: Langfuse (optional)
  What: open-source LLM observability. Records every agent step, prompt, output,
        latency and token count as a trace.
  Why:  in production you must be able to answer "why did the agent do that?".
        Enabled automatically when LANGFUSE_* keys are set.
"""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage

from app.config import get_settings


def get_llm(temperature: float = 0.0) -> BaseChatModel:
    s = get_settings()
    provider = s.llm_provider.lower()

    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=s.llm_model, temperature=temperature, api_key=s.groq_api_key or None, max_retries=3)

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=s.llm_model, temperature=temperature, api_key=s.openai_api_key or None, max_retries=3)

    if provider == "azure":
        from langchain_openai import AzureChatOpenAI

        return AzureChatOpenAI(
            azure_deployment=s.azure_openai_deployment,
            azure_endpoint=s.azure_openai_endpoint,
            api_version=s.azure_openai_api_version,
            api_key=s.azure_openai_api_key or None,
            temperature=temperature,
            max_retries=3,
        )

    raise ValueError(f"Unknown LLM_PROVIDER '{s.llm_provider}'. Use groq, openai or azure.")


def get_callbacks() -> list:
    """Return a Langfuse callback handler if configured, else no callbacks."""
    s = get_settings()
    if not (s.langfuse_public_key and s.langfuse_secret_key):
        return []
    try:  # Langfuse v3
        from langfuse.langchain import CallbackHandler
    except ImportError:
        try:  # Langfuse v2
            from langfuse.callback import CallbackHandler
        except ImportError:
            return []
    return [CallbackHandler()]


FORMAT_REMINDER = (
    "Your previous reply was rejected because it was plain text. "
    "Return the answer ONLY by calling the provided function with valid arguments. No markdown."
)


def _is_format_error(error: Exception) -> bool:
    text = f"{type(error).__name__} {error}"
    return any(k in text for k in ("tool_use_failed", "OutputParserException", "ValidationError", "did not call a tool"))


def invoke_structured(schema, messages: list[BaseMessage], retries: int = 2):
    """Call the LLM and get a validated Pydantic object back.

    Models sometimes answer in markdown instead of calling the structured-output function
    (found by the eval suite). Those format errors are retried with a firm reminder;
    any other error is raised immediately.
    """
    llm = get_llm().with_structured_output(schema)
    for attempt in range(retries + 1):
        try:
            return llm.invoke(messages)
        except Exception as e:
            if attempt == retries or not _is_format_error(e):
                raise
            messages = [*messages, HumanMessage(FORMAT_REMINDER)]