import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph


DEFAULT_SYSTEM_PROMPT = (
    'You are a reference agent used to test integrations over the A2A '
    'protocol. Answer briefly and in the same language as the user.'
)


def _build_model():
    if not os.getenv('OPENAI_API_KEY'):
        return None
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.getenv('OPENAI_MODEL', 'gpt-4o-mini'), temperature=0.3
    )


def _as_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(
            part.get('text', '') if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


class ReferenceAgent:
    def __init__(self) -> None:
        self._model = _build_model()
        self._system_prompt = os.getenv('AGENT_SYSTEM_PROMPT', DEFAULT_SYSTEM_PROMPT)
        builder = StateGraph(MessagesState)
        builder.add_node('respond', self._respond)
        builder.add_edge(START, 'respond')
        self._graph = builder.compile(checkpointer=InMemorySaver())

    @property
    def mode(self) -> str:
        return 'openai' if self._model is not None else 'echo'

    async def _respond(self, state: MessagesState) -> dict:
        if self._model is None:
            last = _as_text(state['messages'][-1].content)
            return {'messages': [AIMessage(content=f'Echo: {last}')]}
        reply = await self._model.ainvoke(
            [SystemMessage(self._system_prompt), *state['messages']]
        )
        return {'messages': [reply]}

    async def answer(self, context_id: str, text: str) -> str:
        result = await self._graph.ainvoke(
            {'messages': [HumanMessage(text)]},
            config={'configurable': {'thread_id': context_id}},
        )
        return _as_text(result['messages'][-1].content)
