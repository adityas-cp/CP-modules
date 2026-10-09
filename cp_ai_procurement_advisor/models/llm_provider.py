# -*- coding: utf-8 -*-
"""Minimal client for OpenAI-compatible chat completion APIs.

Lets the Procurement Advisor run on a provider of your choice (Google Gemini
free tier, Groq, OpenRouter, OpenAI, a local Ollama...) instead of the Odoo AI
service. It converts the message format used by the `ai` module (lists of
text / inline_data / tool_call / tool_result parts) to and from the OpenAI
chat completions format.
"""

import json
import logging
import time
import uuid

import requests

_logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/openai'
DEFAULT_MODEL = 'gemini-flash-latest'
REQUEST_TIMEOUT = 120
RETRY_STATUSES = {429, 500, 502, 503}
RETRY_DELAYS = (5, 15)

# JSON schema keywords understood by every provider; the others (maxLength,
# pattern...) are dropped as some providers reject unknown keywords.
ALLOWED_SCHEMA_KEYS = {'type', 'description', 'enum', 'properties', 'required', 'items', 'minimum', 'maximum', 'anyOf'}


class LLMProviderError(Exception):
    pass


def sanitize_schema(schema):
    if not isinstance(schema, dict):
        return schema
    clean = {}
    for key, value in schema.items():
        if key not in ALLOWED_SCHEMA_KEYS:
            continue
        if key == 'properties':
            clean[key] = {name: sanitize_schema(sub_schema) for name, sub_schema in value.items()}
        elif key == 'items':
            clean[key] = sanitize_schema(value)
        elif key == 'anyOf':
            clean[key] = [sanitize_schema(sub_schema) for sub_schema in value]
        elif key == 'type' and isinstance(value, list):
            types = [t for t in value if t != 'null']
            clean[key] = types[0] if types else 'string'
        else:
            clean[key] = value
    return clean


def _parts_text(parts):
    return "\n".join(str(part.get('text', '')) for part in parts or [] if part.get('type') == 'text')


def to_openai_tools(tools):
    openai_tools = []
    for tool in tools or []:
        function = {'name': tool['name'], 'description': tool.get('instructions') or tool['name']}
        schema = sanitize_schema(tool.get('schema') or {})
        if schema.get('properties'):
            function['parameters'] = {**schema, 'type': 'object'}
        openai_tools.append({'type': 'function', 'function': function})
    return openai_tools


def to_openai_messages(instructions, messages):
    openai_messages = [{'role': 'system', 'content': instructions}] if instructions else []
    for message in messages or []:
        content = message.get('content') or []
        if isinstance(content, str):
            content = [{'type': 'text', 'text': content}]
        if message.get('role') == 'assistant':
            assistant = {'role': 'assistant', 'content': _parts_text(content)}
            tool_calls = []
            for part in content:
                if part.get('type') != 'tool_call':
                    continue
                tool_call = {
                    'id': str(part['call_id']),
                    'type': 'function',
                    'function': {'name': part['name'], 'arguments': json.dumps(part.get('args') or {})},
                }
                if extra_content := (part.get('provider_data') or {}).get('extra_content'):
                    tool_call['extra_content'] = extra_content
                tool_calls.append(tool_call)
            if tool_calls:
                assistant['tool_calls'] = tool_calls
            openai_messages.append(assistant)
            continue

        # user message: tool results first (they answer the previous assistant turn)
        for part in content:
            if part.get('type') == 'tool_result':
                openai_messages.append({
                    'role': 'tool',
                    'tool_call_id': str(part['tool_call_id']),
                    'content': _parts_text(part.get('result')) or ('success' if part.get('success', True) else 'error'),
                })
        user_parts = []
        for part in content:
            if part.get('type') == 'text' and part.get('text'):
                user_parts.append({'type': 'text', 'text': str(part['text'])})
            elif part.get('type') == 'inline_data':
                mimetype = part.get('mimetype') or ''
                if mimetype.startswith('image/'):
                    user_parts.append({'type': 'image_url', 'image_url': {'url': f"data:{mimetype};base64,{part['data']}"}})
                else:
                    user_parts.append({'type': 'text', 'text': f"[An attached {mimetype} file cannot be read by this AI provider.]"})
        if user_parts:
            openai_messages.append({'role': 'user', 'content': user_parts})
    return openai_messages


def from_openai_response(data):
    """Convert an OpenAI chat completion response to an `ai` module assistant message."""
    try:
        message = data['choices'][0]['message']
    except (KeyError, IndexError, TypeError):
        raise LLMProviderError(f"Unexpected response: {str(data)[:500]}")
    parts = []
    content = message.get('content')
    if isinstance(content, list):
        content = "".join(item.get('text', '') for item in content if isinstance(item, dict))
    if content:
        parts.append({'type': 'text', 'text': content})
    for tool_call in message.get('tool_calls') or []:
        function = tool_call.get('function') or {}
        try:
            args = json.loads(function.get('arguments') or '{}')
        except json.JSONDecodeError:
            args = {}
        part = {
            'type': 'tool_call',
            'name': function.get('name'),
            'args': args if isinstance(args, dict) else {},
            'call_id': tool_call.get('id') or f"call_{uuid.uuid4().hex[:16]}",
        }
        if tool_call.get('extra_content'):
            # e.g. Gemini thought signatures, which must be sent back with the call
            part['provider_data'] = {'extra_content': tool_call['extra_content']}
        parts.append(part)
    if not parts:
        parts.append({'type': 'text', 'text': ''})
    return {'role': 'assistant', 'content': parts, 'provider_metadata': {}}


def _error_message(response):
    try:
        data = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(data, list) and data:
        data = data[0]
    error = data.get('error') if isinstance(data, dict) else None
    if isinstance(error, dict):
        return error.get('message') or str(error)
    return str(error or data)[:500]


def chat_completion(api_key, base_url, model, instructions, messages, tools=None):
    body = {
        'model': model,
        'messages': to_openai_messages(instructions, messages),
    }
    if openai_tools := to_openai_tools(tools):
        body['tools'] = openai_tools
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/chat/completions"
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}

    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            response = requests.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as e:
            raise LLMProviderError(f"Cannot reach {url}: {e}")
        if response.status_code == 200:
            return from_openai_response(response.json())
        if response.status_code in RETRY_STATUSES and attempt < len(RETRY_DELAYS):
            _logger.info("AI provider returned %s, retrying in %ss", response.status_code, RETRY_DELAYS[attempt])
            time.sleep(RETRY_DELAYS[attempt])
            continue
        raise LLMProviderError(f"{response.status_code} - {_error_message(response)}")
