# Memory, notes and background jobs

Jig keeps what it knows about you on this computer, in its own database. You can read it, change it and delete it. This is a beta (version 0.1.0b1). Memory search is a word index (SQLite FTS5). Embeddings are not in this version.

The rules for what is sent to a cloud model, and how deletion is done on disk, are in the README under [Safety model](../README.md#safety-model) (the part headed "Memory and notes") and [Using a cloud model](../README.md#using-a-cloud-model). This page is how you use them.

## Memory and notes

Open **Settings > Memory and notes**.

1. **Memories** are what Jig remembers about you. Search with **Search**, or **Show all**. Open one to view it, edit it or delete it.
2. **Tell Jig something to remember** adds one yourself. **Kind** defaults to `fact`. **Tags** are comma-separated.
3. You can also tell Jig in the chat to remember something. Changing or forgetting a memory, when the agent does it because you asked, is reviewed by the Sentinel and may need your OK. Jig does not rewrite your memories by itself.
4. **Jig's notes** are what it writes down for itself while it works, such as research findings and reminders. They are separate from memories.
5. **Delete all notes…** removes every note. Memories stay.
6. **Forget everything…** deletes every memory and note, every conversation, and every finished job with its results. Jig then starts again knowing nothing about you. A conversation Jig is replying in, or a job that is still going, has to finish or be stopped first. Those are left, and Jig says how many it kept.

With a cloud model, memories that were already sent in a request stay with the provider under its terms. Deleting them in Jig removes the copy on this computer.

## Conversations and jobs

**Settings > Conversations and jobs** lists conversations and finished jobs. You can read one and delete one, or delete them all. Deleting a conversation or a finished job removes the copies of its text Jig kept (the conversation, the runs, the steps, and the approval details). Files Jig wrote in its folder stay: those are yours.

**Settings > History** is the audit log. Entries cannot be edited or deleted, so they do not hold what was said. They record what happened, when, and what was decided.

## Schedules (background jobs)

A schedule is a job Jig runs by itself at set times. Each run is an ordinary task, so the usual rules, the Sentinel and approvals apply. The full repeat rules are in the README under [Schedules](../README.md#schedules).

1. Open **Settings > Schedules** and **Add a schedule**.
2. Give it a **Name** and **What should Jig do each time?**
3. Choose **Repeats**: every weekday (Monday to Friday), every day, on days you choose, every few minutes or hours, or a cron expression. For a clock time, set **At** (the form starts at 08:00).
4. Choose **While it runs**:
   - **Just look, don't touch** is the default for a new schedule. Jig can read and take notes. It will not change, send or save anything of yours.
   - **Can act (asks when needed)** may take actions, still subject to the Sentinel and your approval.
5. Choose **Add schedule**. The list shows the next run and how the last one ended.

Pause or resume a schedule from that list, or delete it. **Timezone** on the same page is Jig's timezone for schedules. The default is the computer's timezone. If you change it, schedules that were on the old timezone keep their times of day.

A one-off background job is a goal. Open **What Jig's up to**, describe what Jig should achieve, and choose **Create goal**. Jig plans the steps, works through them in the background, and asks you before anything that needs your OK. **Stop** there stops background jobs after you confirm.

While Jig is paused (the **Pause** button beside **What Jig's up to**), schedules do not start new work.

Closing Jig's window on Windows does not turn Jig off, so schedules still run. `jig stop`, or **Turn Jig off**, does stop them until Jig is running again.
