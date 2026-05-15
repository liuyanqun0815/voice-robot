import argparse
import os
from typing import Any

from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver


@tool
def get_weather(city: str) -> str:
    """Get weather for a given city."""
    return f"{city} 当前天气晴，气温 25C。"


def build_model() -> Any:
    provider = os.getenv("DEMO_MODEL_PROVIDER", "openai")
    model_name = os.getenv("DEMO_MODEL_NAME", "gpt-4o-mini")
    if provider == "openai":
        # e.g. DEMO_MODEL_NAME=gpt-4o-mini
        return init_chat_model(f"openai:{model_name}", temperature=0.2, streaming=True)
    if provider == "anthropic":
        # e.g. DEMO_MODEL_NAME=claude-3-5-sonnet-latest
        return init_chat_model(f"anthropic:{model_name}", temperature=0.2, streaming=True)
    if provider == "google":
        # e.g. DEMO_MODEL_NAME=gemini-2.0-flash
        return init_chat_model(
            model_name,
            model_provider="google-genai",
            temperature=0.2,
            streaming=True,
        )
    raise ValueError(f"Unsupported DEMO_MODEL_PROVIDER: {provider}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepAgent streaming demo")
    parser.add_argument(
        "--prompt",
        default="请告诉我北京天气，并用两句话给出建议。",
        help="User prompt sent to deep agent",
    )
    args = parser.parse_args()

    model = build_model()
    checkpointer = InMemorySaver()

    deep_agent = create_deep_agent(
        model=model,
        tools=[get_weather],
        system_prompt="你是一个简洁、友好的中文助手。",
        checkpointer=checkpointer,
    )

    print("=== DeepAgent 流式输出开始 ===")
    for event in deep_agent.stream(
        {"messages": [{"role": "user", "content": args.prompt}]},
        config={"configurable": {"thread_id": "deepagent-stream-demo"}},
        stream_mode="values",
    ):
        messages = event.get("messages", [])
        if not messages:
            continue
        last_message = messages[-1]
        content = getattr(last_message, "content", "")
        if isinstance(content, str) and content.strip():
            print(content)
    print("=== DeepAgent 流式输出结束 ===")


if __name__ == "__main__":
    main()
