1. Think Before Coding
- State assumptions. If unsure or confused, stop and ask.
- If there are multiple interpretations, present them instead of picking silently.
- If a simpler approach exists, say so and push back when warranted.

2. Simplicity First
- Write the minimum code that solves the problem.
- No unrequested features, single-use abstractions, speculative configurability, or error handling for impossible cases.
- If 200 lines could be 50, rewrite it.

3. Surgical Changes
- Touch only what the request requires. Every changed line should trace back to it.
- Don't refactor, reformat, or "improve" adjacent code. Match existing style.
- Remove only the imports, variables, and functions your changes orphaned. Mention unrelated dead code instead of deleting it.

4. Goal-Driven Execution
- Turn tasks into verifiable goals: "fix the bug" becomes "write a reproducing test, then make it pass."
- For multi-step work, state a short plan with a check per step:
  ```
  1. [Step] → verify: [check]
  2. [Step] → verify: [check]
  ```
- Loop until the checks pass.

5. Accuracy and certain rules
- Before coding or planning always access the relevant docs using webfetch and / or context7
- Do not add co-authored by claude to any commits