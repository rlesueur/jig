# Approvals and safety

Jig asks before it does something that could send, change or delete. You see the question in Jig's corner, with **Yes** and **No**. This page is what that looks like. The full gate (schema, mode, core rules, your rules, the Sentinel, then the approval) is in the README under [Safety model](../README.md#safety-model).

This is a beta (version 0.1.0b1). The checks below are in this version. They are not a promise that a model will always ask for the right thing: read the card, and say no when it is wrong.

## What you are asked

1. **You ask Jig to do something**, in chat or as a background job.
2. **Reading is just reading.** Looking things up, and reading mail, calendars and files, goes ahead in the usual way. **Just look, don't touch** under **Settings > In the conversation** goes further: Jig can look things up and keep its own private notes and memories, and it will not change, send or save anything of yours. That applies to new messages. While it is on, the chat says "Just looking".
3. **Anything that could send, change or delete is checked.** Your own rules are under **Settings > What Jig can do on its own**. Core rules are built in and cannot be switched off there. Sending mail and messages, posting, commenting and changing a calendar always need you: a rule cannot make those automatic.
4. **The Sentinel reviews the action.** The Sentinel is a separate request to the safety checker's model, with no tools and without the agent's conversation. On the card it is **Sentinel's verdict**:
   - **Looks fine** (`allow`)
   - **Ask you first** (`ask_user`)
   - **Do not do this** (`deny`)

   A deny does not go ahead, and you cannot override it. If the Sentinel fails or its answer is not valid, the action does not run. By default the Sentinel is the same model as the agent, still in its own request. Settings shows "same model as the agent" in that case.
5. **You answer on the card.** **Exactly what will happen** lists the details. **Why am I asking?** includes the rule and the Sentinel's reason. **Yes** lets that action run. **No** stops it. A note is optional. Answered questions are kept under **Settings > History > Questions you've answered**.

While a job is waiting, the icon by the clock on Windows can show a notification. It says a job is waiting, not what the job wants to do.

In the terminal, `jig chat` asks `approve <tool>? [y/N]` for the same queue.

## Stop, pause, and carrying on

**Stop the reply** sits beside **What Jig's up to** while Jig is writing. It stops that reply and keeps what had already arrived.

**Pause**, in the same place, pauses Jig. Nothing new starts until you resume, and a job that was running stops at a safe point and carries on when you resume. **Stop** there stops background jobs after you confirm. It lists what it will stop. Anything already finished is kept.

**Step limit.** One request may use up to 12 model calls (`[runtime] max_steps` in `jig.toml`). If it reaches that, Jig makes one more call with no tools, so nothing further can be done, and you get the model's own account of what it did and what is left. In chat, send another message to carry on. A background job says `It stopped at its limit of 12 steps.` (or whatever `max_steps` is).

**The same action again.** If a run makes the same tool call, with the same arguments, and gets the same result 3 times, Jig stops it and says it made that call and got the same result 3 times, so it was not getting anywhere. What it did up to then is kept.

## When a reply gets stuck

Jig watches the reply as it arrives. There is no time limit on a reply. Jig only gives up when the server goes silent (nothing for 600 seconds after asking, or for 120 seconds in the middle of a reply). Those two waits are `first_token_timeout_s` and `liveness_timeout_s`.

Two stops are shown in the conversation, with the text Jig already wrote kept above them:

- **Jig stopped this reply because it was repeating itself.**
- **Jig stopped this reply because it was going round in circles.**

Each offers two buttons:

- **Try again** starts that reply again.
- **Continue anyway** carries on from where it stopped. For a repeat, only a very long exact repeat will stop it after that. For going round in circles, Continue anyway leaves that check alone.

A plan or a Sentinel review that fails the same check is asked once more. If that also fails, the plan fails and the Sentinel does not let the action through.
