# Agentleash

Agentleash decides what an AI agent is allowed to spend, send, and open.

It sits on the tool call. The call is allowed, denied, or held for a person, and the reason is a sentence. The same policy is diffed before it ships, so a reviewer sees what an agent newly gains.

The finance demo runs offline. It does not spend money, send mail, or call a model.

The product title and command slug live in `src/agentleash/brand.py`. The import package uses the same slug. The name is not final, and this project is not published to PyPI.
