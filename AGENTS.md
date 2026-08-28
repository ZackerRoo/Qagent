# Qagent Agent Workflow

## Multi-agent lifecycle

- Use sub-agents only for bounded parallel work with a clear owner and deliverable.
- After a sub-agent finishes, review its result and preserve the useful outcome in
  the parent task, repository files, test output, or a commit.
- Close every completed or aborted sub-agent as soon as it is no longer needed.
- Once the result is integrated and no review or audit is pending, delete that
  sub-agent's session file. Do not retain forked sessions merely as duplicate
  context.
- Never delete the active parent task, an active automation session, a running
  sub-agent, or a session whose result has not yet been reviewed.
- Before deleting sessions, verify that they belong to a sub-agent, end in
  `task_complete` or `turn_aborted`, and are not open by a running process.
- Keep a concise implementation and verification summary in the parent task so
  deleting sub-agent sessions does not remove the project decision record.

## Implementation delegation

- For this project, especially for subsequent implementation work in this
  conversation, the parent task owns requirement decomposition, read-only
  investigation, risk boundaries, review, test acceptance, and integration.
- Delegate actual code and documentation implementation to sub-agents with a
  clear scope and deliverable.
- The parent task may edit directly only for an urgent, very small integration
  fix or when a sub-agent is explicitly blocked, and must state the reason.
- Delegation must not bypass simulated-trading isolation, testing, review, or
  session cleanup.

## Resource discipline

- Avoid `fork_context` for simple tasks. Pass a small, self-contained prompt to
  new sub-agents so the full Qagent conversation is not copied repeatedly.
- At the end of each multi-agent round, report the agents closed and the session
  storage removed.
- Keep the repository `SubagentStop`/`Stop` cleanup hook enabled. It queues
  stopped sub-agents, validates their terminal state, and deletes their Codex
  sessions only after the parent turn has consumed their results.
- Do not use the `superpowers` skill for this project.
