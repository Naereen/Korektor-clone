"""Configuration and transport helpers for Korektor's LLM providers.

This module centralizes the OpenAI-compatible HTTP contract used by OCR,
subject analysis, and automatic correction.  ``ProviderConfig`` stores an
endpoint, model name, authentication environment variable, and TLS policy;
``get_provider`` resolves named configurations while allowing callers to
override a model or URL for local deployments.

The transport helpers cover three payload shapes: text-only chat requests,
vision requests where PDF pages are rendered as base64 JPEG images, and
streaming chat requests that report progress through a callback.  Keeping
these concerns here prevents the workflow modules from duplicating headers,
timeouts, Qwen thinking options, response validation, and token estimation.
Provider-specific failures are deliberately propagated to the batch layers,
which decide whether to retry, reduce context, or stop processing.
"""

import base64
import io
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import pymupdf
import requests


@dataclass
class ProviderConfig:
    name: str
    url: str
    model: str
    api_key_env: Optional[str] = None
    verify_ssl: bool = True

    def api_key(self) -> str:
        """Return the configured API key, or a dummy key for local providers."""
        if not self.api_key_env:
            return "sk-dummy"  # Some providers require a non-empty string even if not checked
        api_key_from_env = os.getenv(self.api_key_env, "sk-dummy")
        # DEBUG:
        # print(f"Using API key from environment variable '{self.api_key_env}'")
        return api_key_from_env


# Registre des providers connus
PROVIDERS: Dict[str, ProviderConfig] = {
    "MISTRAL_LARGE": ProviderConfig(
        name="MISTRAL_LARGE",
        url="https://api.mistral.ai/v1/chat/completions",
        model="mistral-large-latest",
        # model="pixtral-12b-2409", # Using Pixtral for Vision capabilities
        api_key_env="MISTRAL_API_KEY",
    ),
    "MISTRAL_NEMO": ProviderConfig(
        name="MISTRAL_NEMO",
        url="https://api.mistral.ai/v1/chat/completions",
        model="open-mistral-nemo",
        api_key_env="MISTRAL_API_KEY",
    ),
    "MISTRAL_CODESTRAL": ProviderConfig(
        name="MISTRAL_CODESTRAL",
        url="https://codestral.mistral.ai/v1/chat/completions",
        model="codestral-latest",
        api_key_env="CODESTRAL_API_KEY",
    ),
    "LOCAL_QWEN_CODER": ProviderConfig(
        name="LOCAL_QWEN_CODER",
        url="http://127.0.0.1:8080/v1/chat/completions",
        model="qwen3-32b-instruct",
        api_key_env=None,
    ),
    "IRMA_LLMCODE": ProviderConfig(
        name="IRMA_LLMCODE",
        url="http://llmcode.math.unistra.fr:8090/v1/chat/completions",
        model="qwen2.5-coder-instruct",
        api_key_env=None,
    ),
    "PALGANIA_QWEN3": ProviderConfig(
        name="PALGANIA_QWEN3",
        url="https://palgania.ovh:8106/v1/chat/completions",
        model="Qwen3-30B",
        api_key_env="TEXTSYNTH_API_KEY",
        verify_ssl=False,
    ),
    "ILAAS_OSS": ProviderConfig(
        name="ILAAS_OSS",
        url="https://llm.ilaas.fr/v1/chat/completions",
        # model="gpt-oss-120b",
        # model="qwen-3-30b",
        # model="mistral-small-3.2-24b",
        model="qwen-3.6-35b-instruct",
        api_key_env="ILAAS_API_KEY",
    ),
    "OPENAI_MODEL": ProviderConfig(
        name="OPENAI_MODEL",
        url="https://api.openai.com/v1/chat/completions",
        model="gpt-4.1",
        api_key_env="OPENAI_API_KEY",
    ),
    "NVIDIA_MODEL": ProviderConfig(
        name="NVIDIA_MODEL",
        url="https://integrate.api.nvidia.com/v1/chat/completions",
        model="nvidia/nemotron-3-nano-30b-a3b",
        api_key_env="NVIDIA_API_KEY",
    ),
    "UNISTRA_QWEN": ProviderConfig(
        name="UNISTRA_QWEN",
        url="https://conversation.ia.unistra.fr/api/v1/chat/completions",
        model="chat-qwen",
        api_key_env="UNISTRA_API_KEY",
    ),
    "GB10B_QWEN36": ProviderConfig(
        name="GB10B_QWEN36",
        url="http://127.0.0.1:6806/v1/chat/completions",
        model="qwen36",
        api_key_env=None,
    ),
    "GB10B_QWEN": ProviderConfig(
        name="GB10B_QWEN",
        url="http://localhost:6806/v1/chat/completions",
        model="qwen36",
        api_key_env=None,
    ),
}

# DEFAULT_PROVIDER = "MISTRAL_CODESTRAL"
# DEFAULT_PROVIDER = "IRMA_LLMCODE"
# DEFAULT_PROVIDER = "UNISTRA_QWEN"
DEFAULT_PROVIDER = "ILAAS_OSS"
# DEFAULT_PROVIDER = "GB10B_QWEN"
# DEFAULT_PROVIDER = "MISTRAL_NEMO"
# DEFAULT_PROVIDER = "LOCAL_QWEN_CODER"
# DEFAULT_PROVIDER = "MISTRAL_LARGE"
# DEFAULT_PROVIDER = "PALGANIA_QWEN3"


def get_provider(
    name: Optional[str] = DEFAULT_PROVIDER,
    override_model: Optional[str] = None,
    override_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> ProviderConfig:
    """Retourne la configuration d'un provider.

    Args:
        name: Nom du provider dans PROVIDERS (None => DEFAULT_PROVIDER)
        override_model: Remplace le nom de modèle si fourni
        override_url: Remplace l'URL si fourni
        api_key: Forcer une clé spécifique (sinon variable d'env)
    """
    if name is None:
        name = DEFAULT_PROVIDER
    if name not in PROVIDERS:
        raise ValueError(
            f"Provider inconnu: {name}. Providers disponibles: {list(PROVIDERS.keys())}"
        )

    base = PROVIDERS[name]
    model = override_model or base.model
    url = override_url or base.url

    # Create a new instance, but we can't just pass api_key to __init__ because it's not a field
    # We'll rely on the property or the user manual check.
    # To support overrides, we might return a customized object or just use the base.
    # For now, we return base modified.

    return ProviderConfig(
        name=name,
        url=url,
        model=model,
        api_key_env=base.api_key_env,
        verify_ssl=base.verify_ssl,
    )


def pdf_to_base64_images(
    pdf_path: str,
    dpi: int = 120,
    jpg_quality: int = 90,
) -> List[str]:
    """Convertit toutes les pages d'un PDF en images base64 (JPEG)."""
    doc = pymupdf.open(pdf_path)
    images_b64 = []

    for page in doc:
        # Render page to pixmap (image)
        pix = page.get_pixmap(dpi=dpi)
        img_data = pix.tobytes("jpeg", jpg_quality=jpg_quality)
        base64_str = base64.b64encode(img_data).decode("utf-8")
        images_b64.append(base64_str)

    doc.close()

    return images_b64


def ask_llm_with_pdf(
    provider: ProviderConfig,
    pdf_path: str,
    prompt_text: str,
    system_prompt: str = "Tu es une IA expert en OCR et transcription manuscrite. Tu dois transcrire fidèlement le texte. Pas de bavardage.",
    inference_callback: Optional[Callable[[bool], None]] = None,
) -> str:
    """Envoie un PDF (converti en images) à l'API LLM Vision."""

    # 1. Convertir le PDF en images
    print(f"Conversion du PDF {pdf_path} en images...")
    images_b64 = pdf_to_base64_images(pdf_path)

    # 2. Construire le contenu message utilisateur
    # Format standard OpenAI Vision: [{"type": "text", ...}, {"type": "image_url", ...}]
    user_content: List[Dict[str, Any]] = [{"type": "text", "text": prompt_text}]

    for page_number, b64_img in enumerate(images_b64, start=1):
        user_content.append({"type": "text", "text": f"Page {page_number}"})
        user_content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64_img}",
                    "detail": "high",
                },
            }
        )

    payload = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider.api_key()}",
    }

    print(f"Envoi de la requête à {provider.url} (Modèle: {provider.model})...")

    try:
        if inference_callback:
            inference_callback(True)
        try:
            response = requests.post(
                provider.url,
                json=payload,
                headers=headers,
                timeout=600,
                verify=provider.verify_ssl,
            )
        finally:
            if inference_callback:
                inference_callback(False)
        response.raise_for_status()

        result_json = response.json()
        content = result_json["choices"][0]["message"]["content"]
        return content

    except Exception as e:
        print(f"Erreur lors de l'appel LLM: {e}")
        if "response" in locals():
            print(f"Response (si dispo): {response.text}")
        raise


def ask_llm_with_image(
    provider: ProviderConfig,
    image_path: str,
    prompt_text: str,
    system_prompt: str,
    inference_callback: Optional[Callable[[bool], None]] = None,
) -> str:
    """Send one PNG crop to an OpenAI-compatible vision endpoint."""
    with open(image_path, "rb") as image_file:
        image_b64 = base64.b64encode(image_file.read()).decode("utf-8")

    payload = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}",
                            "detail": "high",
                        },
                    },
                ],
            },
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider.api_key()}",
    }

    if inference_callback:
        inference_callback(True)
    try:
        response = requests.post(
            provider.url,
            json=payload,
            headers=headers,
            timeout=600,
            verify=provider.verify_ssl,
        )
        response.raise_for_status()
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("Réponse provider au format inattendu") from error
    finally:
        if inference_callback:
            inference_callback(False)


def ask_llm(
    provider: ProviderConfig,
    prompt_text: str,
    system_prompt: str = "Tu es une IA utile.",
    thinking: bool = True,
) -> str:
    """Envoie une requête texte simple à l'API LLM."""

    payload = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_text},
        ],
    }

    # Handle thinking mode for Qwen models (works with chat_template_kwargs)
    if "QWEN" in provider.model.upper():
        payload["chat_template_kwargs"] = {"enable_thinking": thinking}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider.api_key()}",
    }

    print(f"Envoi de la requête à {provider.url} (Modèle: {provider.model})...")

    try:
        response = requests.post(
            provider.url,
            json=payload,
            headers=headers,
            timeout=600,
            verify=provider.verify_ssl,
        )
        response.raise_for_status()

        result_json = response.json()

        # Check if 'choices' exists
        if "choices" not in result_json:
            print(f"Unexpected API response format: {result_json}")
            raise ValueError(f"API returned unexpected format: {result_json}")

        # Extract content
        content = result_json["choices"][0]["message"]["content"]

        if content is None or content == "":
            print(f"Warning: API returned None/Empty content. Full response: {result_json}")
            return ""

        print(f"DEBUG: Content received length: {len(content)}")
        return content

    except Exception as e:
        print(f"Erreur lors de l'appel LLM: {e}")
        if "response" in locals():
            print(f"Response (si dispo): {response.text}")
        raise


def estimate_text_tokens(text: str) -> int:
    """Estime le nombre de tokens dans un texte."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        # Fallback : 1 token ≈ 4 caractères
        return len(text) // 4


def ask_llm_with_progress(
    provider: ProviderConfig,
    prompt_text: str,
    system_prompt: str = "Tu es une IA utile.",
    thinking: bool = True,
    progress_callback: Optional[Callable[[str], None]] = None,
    inference_callback: Optional[Callable[[bool], None]] = None,
) -> str:
    """
    Appelle le LLM en mode streaming avec suivi de progression.

    Args:
        provider: Configuration du provider LLM
        prompt_text: Texte du prompt
        system_prompt: Prompt système
        thinking: Activer le mode thinking
        progress_callback: Fonction pour afficher la progression (message: str)

    Returns:
        str: Réponse complète du LLM
    """
    # 1. Compter les tokens du prompt
    prompt_tokens = estimate_text_tokens(prompt_text)
    if progress_callback:
        progress_callback(f"[Tokens envoyés: {prompt_tokens}]")

    # 2. Préparer le payload avec stream=True
    payload = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_text},
        ],
        "stream": True,
    }

    # Handle thinking mode for Qwen models
    if "QWEN" in provider.model.upper():
        payload["chat_template_kwargs"] = {"enable_thinking": thinking}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider.api_key()}",
    }

    full_response = ""
    received_tokens = 0
    last_log_time = time.time()

    try:
        if inference_callback:
            inference_callback(True)
        with requests.post(
            provider.url,
            json=payload,
            headers=headers,
            stream=True,
            timeout=600,
            verify=provider.verify_ssl,
        ) as response:
            for chunk in response.iter_lines():
                if not chunk:
                    continue

                # Gérer le format SSE (Server-Sent Events) : "data: {json}"
                chunk_str = chunk.decode("utf-8")

                # Ignorer les chunks [DONE] et les lignes vides
                if chunk_str.strip() == "[DONE]":
                    break

                # Enlever le préfixe "data: " s'il est présent
                if chunk_str.startswith("data: "):
                    chunk_str = chunk_str[6:]  # Enlever "data: " (6 caractères)

                try:
                    chunk_data = json.loads(chunk_str)
                    if "choices" in chunk_data and len(chunk_data["choices"]) > 0:
                        content = chunk_data["choices"][0].get("delta", {}).get("content", "")
                        if content:
                            full_response += content
                            received_tokens += estimate_text_tokens(content)

                            # Log au premier chunk et périodiquement (10s ou 20 tokens)
                            current_time = time.time()
                            if (received_tokens > 0 and received_tokens % 20 == 0) or (
                                current_time - last_log_time >= 10
                            ):
                                if progress_callback:
                                    progress_callback(f"[{received_tokens} tokens reçus]")
                                last_log_time = current_time
                except json.JSONDecodeError:
                    continue

    except Exception as e:
        if progress_callback:
            progress_callback(f"[Erreur: {str(e)}]")
        raise
    finally:
        if inference_callback:
            inference_callback(False)

    if progress_callback:
        progress_callback(f"[Total: {received_tokens} tokens reçus]")

    return full_response


__all__ = [
    "ProviderConfig",
    "PROVIDERS",
    "get_provider",
    "DEFAULT_PROVIDER",
    "ask_llm_with_pdf",
    "ask_llm",
    "ask_llm_with_progress",
]
