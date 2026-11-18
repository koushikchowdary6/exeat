# Comparisons

| | Agentleash | OPA, Cedar | promptfoo, garak |
| --- | --- | --- | --- |
| Question | What may this agent spend, send, and open, and what just changed? | Does this request satisfy a policy you wrote? | Does this model fail a safety eval? |
| When it runs | On the tool call, and again as a diff | Wherever you evaluate the policy | In an eval or a red-team run |
| Spend, recipients, paths | First-class, deny by default | Only if you model them | No |
| Permission drift before merge | Yes | You would build the diff | No |
| Tests whether the model obeys the prompt | No | No | Yes |

OPA and Cedar are general policy languages. They can express an agent policy if you model tools, money, and paths yourself. They do not print the grants an agent just gained.

promptfoo and garak are the right tools when the question is whether a model follows an injection. They do not sit on the tool call, and they do not fail a pull request because a ceiling went up.

Agentleash is the piece between those jobs. It does not replace either of them.
