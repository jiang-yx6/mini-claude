LENGTH_RECOVERY_PROMPT = (
    "Output limit reached. Continue exactly where you left off "
    "— no recap, no apology. Break remaining work into smaller steps if needed."
)

def build_length_recovery_message() -> dict[str, str]:
    """Prompt the model to continue after hitting output token limit."""
    return {"role": "user", "content": LENGTH_RECOVERY_PROMPT}
