import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import test from "node:test"

const runtimePath = new URL("../src/fastapi_webmcp/static/runtime.js", import.meta.url)
const runtimeSource = await readFile(runtimePath, "utf8")
const runtime = await import(
  `data:text/javascript;base64,${Buffer.from(runtimeSource).toString("base64")}`
)

test("authenticated registrations refresh safely and reject redirects", async () => {
  const originalFetch = globalThis.fetch
  const originalLocation = globalThis.location
  globalThis.location = new URL("https://app.example/room")

  const fetches = []
  const registrations = []
  let manifestNumber = 0
  let firstRegistrationSignal = null
  globalThis.fetch = async (url, init) => {
    fetches.push({ url: String(url), init })
    if (String(url).endsWith("/manifest.json")) {
      manifestNumber += 1
      if (manifestNumber === 2) {
        assert.equal(firstRegistrationSignal.aborted, false)
      }
      return new Response(
        JSON.stringify({
          version: 1,
          basePath: "",
          credentials: "omit",
          tools: [
            {
              kind: "request",
              name: `documents.read_${manifestNumber}`,
              description: "Read",
              inputSchema: {
                type: "object",
                properties: { agent_token: { type: "string" } },
                required: [],
              },
              annotations: { readOnlyHint: true, untrustedContentHint: true },
              request: {
                method: "GET",
                path: "/api/document",
                pathParams: [],
                queryParams: [],
                bodyParams: [],
                headerParams: { agent_token: "Authorization" },
              },
            },
          ],
        }),
        { status: 200 },
      )
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }

  try {
    const registration = runtime.registerWebMCP({
      manifestUrl: "/manifest.json",
      modelContext: {
        async registerTool(tool, { signal }) {
          registrations.push({ tool, signal })
        },
      },
      requestHeaders: ({ kind }) => ({ Authorization: `Bearer ${kind}` }),
    })

    const first = await registration.ready
    assert.equal(first.tools[0].name, "documents.read_1")
    firstRegistrationSignal = registrations[0].signal
    assert.equal(firstRegistrationSignal.aborted, false)

    await registrations[0].tool.execute({ agent_token: "agent-controlled" }, {})
    assert.equal(fetches[0].init.redirect, "error")
    assert.equal(fetches[0].init.headers.get("authorization"), "Bearer manifest")
    assert.equal(fetches[1].init.redirect, "error")
    assert.equal(fetches[1].init.credentials, "omit")
    assert.equal(fetches[1].init.headers.get("authorization"), "Bearer tool")

    const refreshed = await registration.refresh()
    assert.equal(refreshed.tools[0].name, "documents.read_2")
    assert.equal(firstRegistrationSignal.aborted, true)
    assert.equal(registrations[1].signal.aborted, false)
    assert.equal(registration.signal.aborted, false)

    registration.abort(new Error("logout"))
    assert.equal(registration.signal.aborted, true)
    assert.equal(registrations[1].signal.aborted, true)
  } finally {
    globalThis.fetch = originalFetch
    globalThis.location = originalLocation
  }
})

test("cross-origin manifests are rejected before auth headers are resolved", async () => {
  const originalLocation = globalThis.location
  globalThis.location = new URL("https://app.example/room")
  let headerCalls = 0
  try {
    const registration = runtime.registerWebMCP({
      manifestUrl: "https://evil.example/manifest.json",
      modelContext: { async registerTool() {} },
      requestHeaders() {
        headerCalls += 1
        return { Authorization: "Bearer secret" }
      },
    })

    await assert.rejects(registration.ready, /another origin/)
    assert.equal(headerCalls, 0)
  } finally {
    globalThis.location = originalLocation
  }
})
