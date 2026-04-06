from app.core.config import get_settings
from app.services.chat_service import ChatService


def test_fallback_answer_without_contexts():
    answer = ChatService._fallback_answer("where retry?", [])
    assert "未检索到可用上下文" in answer
    assert "Top-K" in answer


def test_fallback_answer_with_contexts_is_structured():
    contexts = [
        {
            "ref": "src/payment/retry.py:10-24 (retry_handler)",
            "score": 0.82,
            "snippet": "def retry_handler():\n    return run_retry_policy()\n",
        },
        {
            "ref": "src/payment/service.py:88-110 (PaymentService.pay)",
            "score": 0.31,
            "snippet": "class PaymentService:\n    def pay(self):\n        pass\n",
        },
    ]
    answer = ChatService._fallback_answer("支付重试逻辑在哪里？", contexts)

    assert "当前未使用 LLM（可能未启用或调用失败）" in answer
    assert "[高相关]" in answer
    assert "[中相关]" in answer
    assert "片段摘要：def retry_handler():" in answer
    assert "建议：" in answer


def test_generate_answer_disabled_provider_returns_reason(monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDER", "none")
    monkeypatch.setenv("CHAT_API_BASE", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    get_settings.cache_clear()

    result = ChatService().generate_answer("where retry?", [])
    assert result.answer_mode == "fallback"
    assert result.provider in {"none", "disabled"}
    assert result.error == "chat provider is disabled"
    get_settings.cache_clear()


def test_generate_answer_openai_falls_back_to_secondary_wire(monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CHAT_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_WIRE_API", "responses")
    get_settings.cache_clear()

    def _raise_responses(self, question, contexts):
        raise RuntimeError("404 not found")

    def _ok_chat(self, question, contexts):
        return "LLM answer from chat_completions"

    monkeypatch.setattr(ChatService, "_openai_responses_answer", _raise_responses)
    monkeypatch.setattr(ChatService, "_openai_chat_completions_answer", _ok_chat)

    result = ChatService().generate_answer("where retry?", [{"ref": "a", "snippet": "b", "score": 1}])
    assert result.answer_mode == "model"
    assert result.wire_api == "chat_completions"
    assert result.answer == "LLM answer from chat_completions"
    get_settings.cache_clear()


def test_generate_answer_openai_reports_error_when_both_wires_fail(monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CHAT_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_WIRE_API", "chat_completions")
    get_settings.cache_clear()

    def _raise_chat(self, question, contexts):
        raise RuntimeError("chat failed")

    def _raise_responses(self, question, contexts):
        raise RuntimeError("responses failed")

    monkeypatch.setattr(ChatService, "_openai_chat_completions_answer", _raise_chat)
    monkeypatch.setattr(ChatService, "_openai_responses_answer", _raise_responses)

    result = ChatService().generate_answer("where retry?", [{"ref": "a", "snippet": "b", "score": 1}])
    assert result.answer_mode == "fallback"
    assert result.provider == "openai_compatible"
    assert "chat_completions: chat failed" in (result.error or "")
    assert "responses: responses failed" in (result.error or "")
    get_settings.cache_clear()


def test_parse_responses_answer_prefers_output_text():
    payload = {"output_text": "这是 responses API 的答案"}
    answer = ChatService._parse_responses_answer(payload)
    assert answer == "这是 responses API 的答案"


def test_parse_responses_answer_from_output_content():
    payload = {
        "output": [
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "来自 output.content 的答案"}],
            }
        ]
    }
    answer = ChatService._parse_responses_answer(payload)
    assert answer == "来自 output.content 的答案"


def test_endpoint_candidates_add_v1_fallback():
    endpoints = ChatService._endpoint_candidates("https://sub.jlypx.de", "responses")
    assert endpoints == [
        "https://sub.jlypx.de/v1/responses",
        "https://sub.jlypx.de/responses",
    ]


def test_endpoint_candidates_keep_single_when_base_has_v1():
    endpoints = ChatService._endpoint_candidates("https://sub.jlypx.de/v1", "chat/completions")
    assert endpoints == [
        "https://sub.jlypx.de/v1/chat/completions",
    ]


def test_build_user_prompt_includes_project_guidance():
    prompt = ChatService._build_user_prompt(
        question="发布流程是什么？",
        contexts=[{"ref": "ops/runbook.md", "snippet": "1. build 2. deploy"}],
        project_instructions="回答必须中文，并给出步骤。",
        project_memories=["部署前必须跑冒烟测试", "变更窗口为周三晚8点"],
    )
    assert "Project Instructions:" in prompt
    assert "回答必须中文，并给出步骤。" in prompt
    assert "Project Memories:" in prompt
    assert "部署前必须跑冒烟测试" in prompt
    assert "变更窗口为周三晚8点" in prompt
