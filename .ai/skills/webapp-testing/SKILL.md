---
id: webapp-testing
name: Web app testing
description: |
  Drive Playwright end-to-end tests against a local web app. Covers
  scaffolding `playwright.config.ts`, writing a smoke test, and
  interpreting failed traces.
version: 0.2.0
tags: [testing, playwright, e2e, browser, smoke]
agents: ["*"]
triggers:
  - e2e
  - playwright
  - browser test
  - smoke test
  - ui test
  - end to end
inputModes: [text/plain]
outputModes: [text/plain, application/json]
mcp_servers: [searchbox, shellbox]
examples:
  - "Write a Playwright smoke test for OpenWebUI on localhost:3000."
  - "Run the e2e suite and explain any failures."
  - "Add a Playwright test that verifies the /healthz endpoint."
securityRequirements: []
---

# Web app testing

## Purpose

Plan, write, and debug Playwright tests for a web app reachable at
some `BASE_URL`. The agent never opens a real browser on the host;
instead it drives Playwright headlessly through the `shellbox` MCP
server (which has the right runtime image preinstalled).

## When to use

- The user wants an end-to-end test for a UI flow.
- A test is failing and the user wants the trace explained.
- The user asks "is the app behaving on localhost:NNNN".

## Inputs

- `BASE_URL` (e.g. `http://localhost:3000`).
- A description of the flow (login, then click X, expect Y).
- Optional: existing `playwright.config.ts` + selectors.

## Procedure

1. **Confirm the app is reachable** (`curl -fsS $BASE_URL` via
   shellbox). If not, stop and ask the user to start it.
2. **Scaffold** if no config exists:

   ```ts
   import { defineConfig, devices } from '@playwright/test';
   export default defineConfig({
     testDir: 'tests/e2e',
     use: { baseURL: process.env.BASE_URL ?? 'http://localhost:3000' },
     projects: [
       { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
     ],
   });
   ```

3. **Write the test** as a single `.spec.ts` file. Prefer
   role-based selectors (`getByRole`, `getByLabel`) over CSS classes.
4. **Run it** with `npx playwright test --reporter=list`.
5. **On failure**: read the `test-results/<spec>/error-context.md`
   and the trace zip; cite the exact step that broke.

## Output

- The test file (full contents).
- A one-line command to run it.
- If diagnosing: the failing step + a concrete fix (selector change,
  retry, wait condition).

## Quality bar

- Selectors are role-based, not brittle XPath.
- No `page.waitForTimeout()` — use `expect(...).toBeVisible()` or
  `page.waitForLoadState('networkidle')`.
- Each test isolates state; no leftovers between runs.

## Anti-patterns

- **Do not** install Chrome on the host. Use `npx playwright install
  chromium` inside shellbox if needed.
- **Do not** assert against random IDs or auto-generated CSS classes.
- **Do not** test the framework; test your app's behaviour.
