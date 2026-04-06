from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import get_settings


@dataclass
class ChatGenerationResult:
    answer: str
    answer_mode: str
    model: str | None = None
    provider: str | None = None
    wire_api: str | None = None
    error: str | None = None


class ChatService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def generate_answer(
        self,
        question: str,
        contexts: list[dict],
        project_instructions: str | None = None,
        project_memories: list[str] | None = None,
    ) -> ChatGenerationResult:
        resolved_memories = project_memories or []
        provider = self.settings.resolved_chat_provider()
        if provider in {"", "none", "disabled"}:
            return ChatGenerationResult(
                answer=self._fallback_answer(question, contexts),
                answer_mode="fallback",
                provider=provider or "none",
                error="chat provider is disabled",
            )

        if provider == "openai_compatible":
            if not self.settings.resolved_chat_api_base():
                return ChatGenerationResult(
                    answer=self._fallback_answer(question, contexts),
                    answer_mode="fallback",
                    provider=provider,
                    model=self.settings.resolved_chat_model(),
                    error="missing CHAT_API_BASE/OPENAI_BASE_URL",
                )
            try:
                answer, wire_api = self._openai_compatible_answer(
                    question=question,
                    contexts=contexts,
                    project_instructions=project_instructions,
                    project_memories=resolved_memories,
                )
                return ChatGenerationResult(
                    answer=answer,
                    answer_mode="model",
                    model=self.settings.resolved_chat_model(),
                    provider=provider,
                    wire_api=wire_api,
                )
            except Exception as exc:
                return ChatGenerationResult(
                    answer=self._fallback_answer(question, contexts),
                    answer_mode="fallback",
                    model=self.settings.resolved_chat_model(),
                    provider=provider,
                    wire_api=self.settings.resolved_openai_wire_api(),
                    error=self._normalize_error(str(exc)),
                )

        return ChatGenerationResult(
            answer=self._fallback_answer(question, contexts),
            answer_mode="fallback",
            provider=provider,
            error=f"unsupported chat provider: {provider}",
        )

    def _openai_compatible_answer(
        self,
        question: str,
        contexts: list[dict],
        project_instructions: str | None,
        project_memories: list[str],
    ) -> tuple[str, str]:
        preferred_wire = self.settings.resolved_openai_wire_api()
        wire_candidates = [preferred_wire]
        if preferred_wire == "responses":
            wire_candidates.append("chat_completions")
        else:
            wire_candidates.append("responses")

        errors: list[str] = []
        for wire_api in wire_candidates:
            try:
                if wire_api == "responses":
                    return (
                        self._invoke_openai_responses(
                            question=question,
                            contexts=contexts,
                            project_instructions=project_instructions,
                            project_memories=project_memories,
                        ),
                        "responses",
                    )
                return (
                    self._invoke_openai_chat_completions(
                        question=question,
                        contexts=contexts,
                        project_instructions=project_instructions,
                        project_memories=project_memories,
                    ),
                    "chat_completions",
                )
            except Exception as exc:
                errors.append(f"{wire_api}: {self._normalize_error(str(exc))}")

        raise RuntimeError(" | ".join(errors))

    def _invoke_openai_chat_completions(
        self,
        question: str,
        contexts: list[dict],
        project_instructions: str | None,
        project_memories: list[str],
    ) -> str:
        try:
            return self._openai_chat_completions_answer(
                question=question,
                contexts=contexts,
                project_instructions=project_instructions,
                project_memories=project_memories,
            )
        except TypeError as exc:
            text = str(exc)
            if "unexpected keyword argument" in text or "positional argument" in text:
                return self._openai_chat_completions_answer(question, contexts)  # type: ignore[misc]
            raise

    def _invoke_openai_responses(
        self,
        question: str,
        contexts: list[dict],
        project_instructions: str | None,
        project_memories: list[str],
    ) -> str:
        try:
            return self._openai_responses_answer(
                question=question,
                contexts=contexts,
                project_instructions=project_instructions,
                project_memories=project_memories,
            )
        except TypeError as exc:
            text = str(exc)
            if "unexpected keyword argument" in text or "positional argument" in text:
                return self._openai_responses_answer(question, contexts)  # type: ignore[misc]
            raise

    def _openai_chat_completions_answer(
        self,
        question: str,
        contexts: list[dict],
        project_instructions: str | None,
        project_memories: list[str],
    ) -> str:
        base_url = self.settings.resolved_chat_api_base()
        if not base_url:
            raise RuntimeError("chat_api_base is required for openai_compatible provider")

        headers = {"Content-Type": "application/json"}
        api_key = self.settings.resolved_chat_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        user_prompt = self._build_user_prompt(
            question=question,
            contexts=contexts,
            project_instructions=project_instructions,
            project_memories=project_memories,
        )

        payload = {
            "model": self.settings.resolved_chat_model(),
            "temperature": self.settings.chat_temperature,
            "max_tokens": self.settings.chat_max_tokens,
            "messages": [
                {"role": "system", "content": "You produce concise, grounded engineering answers with citations."},
                {"role": "user", "content": user_prompt},
            ],
        }

        data = self._post_json_with_candidates(
            endpoints=self._endpoint_candidates(base_url, "chat/completions"),
            headers=headers,
            payload=payload,
        )

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("chat response has no choices")
        content = choices[0].get("message", {}).get("content")
        if not content:
            raise RuntimeError("chat response missing message content")
        return str(content)

    def _openai_responses_answer(
        self,
        question: str,
        contexts: list[dict],
        project_instructions: str | None,
        project_memories: list[str],
    ) -> str:
        base_url = self.settings.resolved_chat_api_base()
        if not base_url:
            raise RuntimeError("chat_api_base is required for openai_compatible provider")

        headers = {"Content-Type": "application/json"}
        api_key = self.settings.resolved_chat_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        user_prompt = self._build_user_prompt(
            question=question,
            contexts=contexts,
            project_instructions=project_instructions,
            project_memories=project_memories,
        )
        payload = {
            "model": self.settings.resolved_chat_model(),
            "temperature": self.settings.chat_temperature,
            "max_output_tokens": self.settings.chat_max_tokens,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {"type": "input_text", "text": "You produce concise, grounded engineering answers with citations."}
                    ],
                },
                {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
            ],
        }

        data = self._post_json_with_candidates(
            endpoints=self._endpoint_candidates(base_url, "responses"),
            headers=headers,
            payload=payload,
        )
        return self._parse_responses_answer(data)

    @staticmethod
    def _build_user_prompt(
        question: str,
        contexts: list[dict],
        project_instructions: str | None,
        project_memories: list[str],
    ) -> str:
        style = ChatService._prompt_style(question)
        guidance_lines: list[str] = []
        if project_instructions:
            guidance_lines.append("Project Instructions:")
            guidance_lines.append(project_instructions)
            guidance_lines.append("")
        if project_memories:
            guidance_lines.append("Project Memories:")
            for idx, memory in enumerate(project_memories, start=1):
                guidance_lines.append(f"{idx}. {memory}")
            guidance_lines.append("")
        context_lines: list[str] = []
        for idx, ctx in enumerate(contexts, start=1):
            ref = ctx.get("ref", "unknown")
            snippet = str(ctx.get("snippet", "")).strip()
            if len(snippet) > 900:
                snippet = snippet[:900]
            context_lines.append(f"[{idx}] {ref}\n{snippet}")
        guidance_block = "\n".join(guidance_lines) if guidance_lines else ""
        return (
            "You are a grounded assistant for code and knowledge-base Q&A. "
            f"Preferred answer style: {style}. "
            "Treat project instructions and memories as mandatory constraints. "
            "Answer only using provided contexts. Cite references like [1], [2]. "
            "If evidence is insufficient, explicitly say insufficient evidence. "
            "If evidence conflicts, clearly describe the conflict and list both citations.\n\n"
            f"{guidance_block}"
            f"Question:\n{question}\n\n"
            "Contexts:\n"
            + "\n\n".join(context_lines)
        )

    @staticmethod
    def _parse_responses_answer(data: dict) -> str:
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        output_items = data.get("output") or []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            if item.get("role") and item.get("role") != "assistant":
                continue
            for chunk in item.get("content") or []:
                if not isinstance(chunk, dict):
                    continue
                text = chunk.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()

        # Some gateways may still return chat-completions style payloads.
        choices = data.get("choices") or []
        if choices:
            content = choices[0].get("message", {}).get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()

        raise RuntimeError("responses payload missing assistant output")

    @staticmethod
    def _fallback_answer(question: str, contexts: list[dict]) -> str:
        if not contexts:
            return "未检索到可用上下文，无法生成可靠答案。建议：先确认项目已完成同步，再增大 Top-K 或补充更具体关键词。"

        ranked = sorted(contexts, key=lambda item: float(item.get("score", 0.0)), reverse=True)
        insufficient = len(ranked) < 2 or float(ranked[0].get("score", 0.0)) < 0.25
        conflict = ChatService._has_conflict(ranked[:5])
        lines = [
            f"检索问题：{question}",
            "",
            "当前未使用 LLM（可能未启用或调用失败），以下是按相关度排序的代码线索：",
        ]
        for idx, ctx in enumerate(ranked[:5], start=1):
            score = float(ctx.get("score", 0.0))
            ref = str(ctx.get("ref", "unknown"))
            snippet = str(ctx.get("snippet", "")).strip()
            hint = ChatService._snippet_head(snippet)
            lines.append(f"{idx}. [{ChatService._confidence_label(score)}] {ref} (score={score:.4f})")
            if hint:
                lines.append(f"   片段摘要：{hint}")

        lines.append("")
        if insufficient:
            lines.append("证据覆盖偏低：当前可用片段较少或相关度不足，建议补充关键词后重试。")
        if conflict:
            lines.append("检测到潜在证据冲突：片段来源指向不同实现，请人工核对上下文。")
        lines.append("建议：在“上下文预览”中查看完整片段；如果不准确，请增大 Top-K 或补充函数名/文件名关键词。")
        return "\n".join(lines)

    @staticmethod
    def _snippet_head(snippet: str) -> str:
        for line in snippet.splitlines():
            normalized = " ".join(line.strip().split())
            if normalized:
                return normalized[:120]
        return ""

    @staticmethod
    def _confidence_label(score: float) -> str:
        if score >= 0.6:
            return "高相关"
        if score >= 0.3:
            return "中相关"
        return "弱相关"

    @staticmethod
    def _prompt_style(question: str) -> str:
        q = (question or "").lower()
        if any(token in q for token in ["总结", "摘要", "概览", "summary"]):
            return "summary"
        if any(token in q for token in ["步骤", "如何", "怎么", "排查", "runbook"]):
            return "step_by_step"
        if any(token in q for token in ["规范", "政策", "policy", "合规"]):
            return "policy"
        return "concise_engineering"

    @staticmethod
    def _has_conflict(contexts: list[dict]) -> bool:
        if len(contexts) < 2:
            return False
        refs = [str(ctx.get("ref", "")) for ctx in contexts]
        # Lightweight conflict heuristic: multiple top contexts from distinct files with close scores.
        top_scores = [float(ctx.get("score", 0.0)) for ctx in contexts[:3]]
        if len({ref.split(":", 1)[0] for ref in refs[:3] if ref}) >= 2:
            if top_scores and max(top_scores) - min(top_scores) < 0.08:
                return True
        return False

    @staticmethod
    def _normalize_error(error: str) -> str:
        compact = " ".join((error or "").split())
        if len(compact) <= 300:
            return compact
        return compact[:300]

    @staticmethod
    def _endpoint_candidates(base_url: str, path: str) -> list[str]:
        base = (base_url or "").strip().rstrip("/")
        route = path.lstrip("/")
        candidates: list[str] = []

        if not base:
            return candidates

        if not base.lower().endswith("/v1"):
            candidates.append(f"{base}/v1/{route}")
        candidates.append(f"{base}/{route}")

        deduped: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                deduped.append(candidate)
        return deduped

    def _post_json_with_candidates(
        self,
        endpoints: list[str],
        headers: dict[str, str],
        payload: dict,
    ) -> dict:
        if not endpoints:
            raise RuntimeError("no endpoint candidates")

        errors: list[str] = []
        with httpx.Client(timeout=self.settings.embedding_timeout_sec) as client:
            for endpoint in endpoints:
                try:
                    resp = client.post(endpoint, headers=headers, json=payload)
                    resp.raise_for_status()
                    try:
                        data = resp.json()
                    except ValueError:
                        raise RuntimeError(
                            f"invalid JSON response ({self._response_preview(resp)})"
                        ) from None
                    if not isinstance(data, dict):
                        raise RuntimeError("response payload is not an object")
                    return data
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    errors.append(
                        f"{endpoint}: HTTP {status} ({self._response_preview(exc.response)})"
                    )
                except Exception as exc:
                    errors.append(f"{endpoint}: {self._normalize_error(str(exc))}")

        raise RuntimeError(" ; ".join(errors))

    @staticmethod
    def _response_preview(resp: httpx.Response) -> str:
        content_type = (resp.headers.get("content-type") or "").strip()
        body = " ".join((resp.text or "").split())
        if len(body) > 160:
            body = body[:160]
        if not body:
            body = "<empty body>"
        if content_type:
            return f"content-type={content_type}, body={body}"
        return f"body={body}"
