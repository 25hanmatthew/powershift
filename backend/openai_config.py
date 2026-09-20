"""Shared text-model configuration for the assistant and planning tools."""
import os

DEFAULT_OPENAI_MODEL = 'gpt-6-astra'


def openai_model(*, chat=False):
    return (os.getenv('OPENAI_CHAT_MODEL') if chat else None) or os.getenv('OPENAI_MODEL') or DEFAULT_OPENAI_MODEL


def response_settings(model, max_output_tokens=None):
    settings = {'model': model}
    if model.startswith('gpt-6-astra'):
        settings['reasoning'] = {'effort': 'low'}
        if max_output_tokens is not None:
            # Responses counts reasoning tokens in the output budget too.
            max_output_tokens = max(max_output_tokens, 8192)
    if max_output_tokens is not None:
        settings['max_output_tokens'] = max_output_tokens
    return settings
