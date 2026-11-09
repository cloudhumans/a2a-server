import asyncio
import logging
import re

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatus

from app.agent import ReferenceAgent


logger = logging.getLogger(__name__)

DIRECTIVE = re.compile(r'\[(delay|input-required|fail)(?:=(\d+))?\]', re.IGNORECASE)


def parse_directives(text: str) -> tuple[dict[str, int | bool], str]:
    directives: dict[str, int | bool] = {}
    for name, value in DIRECTIVE.findall(text):
        directives[name.lower()] = int(value) if value else True
    return directives, DIRECTIVE.sub('', text).strip()


class ReferenceAgentExecutor(AgentExecutor):
    def __init__(
        self,
        agent: ReferenceAgent,
        default_delay_seconds: int,
        progress_interval_seconds: int,
    ) -> None:
        self._agent = agent
        self._default_delay = default_delay_seconds
        self._progress_interval = progress_interval_seconds

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        task_id = context.task_id
        context_id = context.context_id
        if not message or not task_id or not context_id:
            return

        if context.current_task is None:
            await event_queue.enqueue_event(
                Task(
                    id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                    history=[message],
                )
            )

        updater = TaskUpdater(event_queue, task_id, context_id)
        directives, text = parse_directives(context.get_user_input())
        await updater.start_work(self._message(updater, 'Request received, working on it.'))

        delay = directives.get('delay', self._default_delay)
        if delay is True:
            delay = self._default_delay
        await self._wait(updater, int(delay))

        if directives.get('fail'):
            await updater.failed(self._message(updater, 'Simulated failure requested with [fail].'))
            return

        if directives.get('input-required'):
            await updater.requires_input(
                self._message(updater, 'I need one more detail to continue: what is the order number?')
            )
            return

        try:
            reply = await self._agent.answer(context_id, text or '(empty message)')
        except Exception as error:
            logger.exception('Agent failed for task %s', task_id)
            await updater.failed(self._message(updater, f'Agent error: {error}'))
            return

        await updater.add_artifact([Part(text=reply)], name='response', last_chunk=True)
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id or '', context.context_id or '')
        await updater.cancel()

    async def _wait(self, updater: TaskUpdater, seconds: int) -> None:
        elapsed = 0
        while elapsed < seconds:
            step = min(self._progress_interval, seconds - elapsed)
            await asyncio.sleep(step)
            elapsed += step
            if elapsed < seconds:
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=self._message(updater, f'Still working: {elapsed}s of {seconds}s.'),
                )

    @staticmethod
    def _message(updater: TaskUpdater, text: str):
        return updater.new_agent_message(parts=[Part(text=text)])
