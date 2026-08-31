const statusElement = document.querySelector("#webmcp-status")
const toolsElement = document.querySelector("#webmcp-tools")
const manifestElement = document.querySelector('meta[name="fastapi-webmcp-manifest"]')
const context = document.modelContext

if (!context || typeof context.registerTool !== "function") {
  statusElement.textContent =
    "WebMCP is not available in this browser. The regular FastAPI application still works."
  toolsElement.replaceChildren(listItem("No browser tools registered."))
} else {
  const lifetime = new AbortController()
  window.addEventListener("pagehide", () => lifetime.abort(), { once: true })

  try {
    const manifestUrl = new URL(manifestElement?.content ?? "./manifest.json", location.href)
    const response = await fetch(manifestUrl, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
      signal: lifetime.signal,
    })
    if (!response.ok) {
      throw new Error(`Could not load the WebMCP manifest (${response.status})`)
    }

    const manifest = await response.json()
    const applicationBase = applicationBaseUrl(manifest.basePath)
    await Promise.all(
      manifest.tools.map((tool) =>
        context.registerTool(
          {
            name: tool.name,
            description: tool.description,
            inputSchema: tool.inputSchema,
            annotations: tool.annotations,
            execute: (input, options) =>
              executeRequestTool(
                tool,
                input,
                options,
                lifetime.signal,
                applicationBase,
                manifest.credentials,
              ),
          },
          { signal: lifetime.signal },
        ),
      ),
    )

    statusElement.textContent = `${manifest.tools.length} WebMCP tools registered.`
    toolsElement.replaceChildren(...manifest.tools.map((tool) => listItem(tool.name, true)))
  } catch (error) {
    lifetime.abort()
    statusElement.textContent = `WebMCP registration failed: ${errorMessage(error)}`
    toolsElement.replaceChildren(listItem("Registration failed."))
    console.error(error)
  }
}

async function executeRequestTool(
  tool,
  input,
  options,
  lifetimeSignal,
  applicationBase,
  credentials,
) {
  try {
    const args = input && typeof input === "object" ? input : {}
    const path = substitutePath(tool.request.path, tool.request.pathParams, args)
    if (path.error) return errorResult(path)

    const url = new URL(path.value.replace(/^\//, ""), applicationBase)
    if (url.origin !== location.origin || !url.pathname.startsWith(applicationBase.pathname)) {
      return errorResult({ error: "Refused a tool request outside the FastAPI application." })
    }
    for (const name of tool.request.queryParams) {
      if (args[name] !== undefined && args[name] !== null) {
        const values = Array.isArray(args[name]) ? args[name] : [args[name]]
        for (const value of values) url.searchParams.append(name, String(value))
      }
    }

    const method = tool.request.method
    if (!new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]).has(method)) {
      return errorResult({ error: `Unsupported HTTP method: ${method}` })
    }

    const callSignal = options?.signal
    const signal = callSignal
      ? AbortSignal.any([lifetimeSignal, callSignal])
      : lifetimeSignal
    const request = {
      method,
      headers: { Accept: "application/json" },
      credentials: credentials === "same-origin" ? "same-origin" : "omit",
      signal,
    }
    const body = requestBody(tool.request, args)
    if (body.present) {
      request.headers["Content-Type"] = "application/json"
      request.body = JSON.stringify(body.value)
    }

    const response = await fetch(url, request)
    const text = await response.text()
    const result = { status: response.status, body: parseJson(text) }
    return response.ok ? textResult(result) : errorResult(result)
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      return errorResult({ error: "cancelled" })
    }
    return errorResult({ error: errorMessage(error) })
  }
}

function applicationBaseUrl(basePath) {
  const normalized = typeof basePath === "string" ? basePath.replace(/^\/+|\/+$/g, "") : ""
  return new URL(normalized ? `/${normalized}/` : "/", location.origin)
}

function substitutePath(template, names, args) {
  let path = template
  for (const name of names) {
    const value = args[name]
    if (value === undefined || value === null || String(value) === "") {
      return { error: `${name} is required`, field: name }
    }
    path = path.replaceAll(`{${name}}`, encodeURIComponent(String(value)))
  }
  if (/\{[^}]+\}/.test(path)) {
    return { error: `Unresolved path parameter in ${path}` }
  }
  return { value: path }
}

function requestBody(request, args) {
  if (request.bodyValueParam) {
    return { present: args[request.bodyValueParam] !== undefined, value: args[request.bodyValueParam] }
  }
  if (request.bodyParams.length === 0) return { present: false }
  const value = {}
  for (const name of request.bodyParams) {
    if (args[name] !== undefined) value[name] = args[name]
  }
  return { present: true, value }
}

function parseJson(text) {
  if (text.trim() === "") return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function textResult(value) {
  return { content: [{ type: "text", text: JSON.stringify(value) }] }
}

function errorResult(value) {
  return { ...textResult(value), isError: true }
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error)
}

function listItem(text, code = false) {
  const item = document.createElement("li")
  if (code) {
    const element = document.createElement("code")
    element.textContent = text
    item.append(element)
  } else {
    item.textContent = text
  }
  return item
}
