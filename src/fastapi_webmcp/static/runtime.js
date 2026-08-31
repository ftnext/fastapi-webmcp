const ALLOWED_METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"])

/**
 * Register a FastAPI-generated manifest without assuming a frontend framework.
 * Client tools dispatch only to handlers explicitly supplied by this page.
 */
export function registerWebMCP({
  manifestUrl = "./manifest.json",
  handlers = {},
  modelContext = typeof document === "undefined" ? null : document.modelContext,
  signal: parentSignal,
} = {}) {
  const lifetime = new AbortController()
  if (parentSignal) {
    if (parentSignal.aborted) lifetime.abort(parentSignal.reason)
    else parentSignal.addEventListener("abort", () => lifetime.abort(parentSignal.reason), {
      once: true,
    })
  }

  const ready = startRegistration({
    manifestUrl,
    handlers,
    modelContext,
    signal: lifetime.signal,
  }).catch((error) => {
    lifetime.abort(error)
    throw error
  })

  return {
    signal: lifetime.signal,
    ready,
    abort: (reason) => lifetime.abort(reason),
  }
}

async function startRegistration({ manifestUrl, handlers, modelContext, signal }) {
  if (!modelContext || typeof modelContext.registerTool !== "function") {
    return { supported: false, manifest: null, tools: [] }
  }

  const url = new URL(manifestUrl, location.href)
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    signal,
  })
  if (!response.ok) {
    throw new Error(`Could not load the WebMCP manifest (${response.status})`)
  }

  const manifest = await response.json()
  const applicationBase = applicationBaseUrl(manifest.basePath)
  await Promise.all(
    manifest.tools.map((tool) =>
      modelContext.registerTool(
        {
          name: tool.name,
          description: tool.description,
          inputSchema: tool.inputSchema,
          annotations: tool.annotations,
          execute: (input, options) =>
            executeTool({
              tool,
              input,
              callSignal: options?.signal,
              lifetimeSignal: signal,
              applicationBase,
              credentials: manifest.credentials,
              context: manifest.context ?? {},
              handlers,
            }),
        },
        { signal },
      ),
    ),
  )
  return { supported: true, manifest, tools: manifest.tools }
}

async function executeTool(options) {
  try {
    const tool = options.tool
    const args = options.input && typeof options.input === "object" ? options.input : {}
    const signal = options.callSignal
      ? AbortSignal.any([options.lifetimeSignal, options.callSignal])
      : options.lifetimeSignal

    if (tool.kind === "static") return textResult(tool.staticText)
    if (tool.kind === "client") {
      return await executeClientTool(tool, args, signal, options)
    }
    return await executeRequestTool(tool, args, signal, options)
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      return errorResult({ error: "cancelled" })
    }
    return errorResult({ error: errorMessage(error) })
  }
}

async function executeClientTool(tool, args, signal, options) {
  const handler = Object.hasOwn(options.handlers, tool.action)
    ? options.handlers[tool.action]
    : null
  if (typeof handler !== "function") {
    return errorResult({ error: `No client handler is registered for action: ${tool.action}` })
  }
  const value = await handler(args, {
    signal,
    tool,
    context: options.context,
  })
  if (value === undefined) {
    return errorResult({ error: `${tool.name} returned no result` })
  }
  return isToolResult(value) ? value : textResult(value)
}

async function executeRequestTool(tool, args, signal, options) {
  const requestMapping = tool.request
  const boundPathParams = requestMapping.boundPathParams ?? {}
  const pathArgs = { ...args, ...boundPathParams }
  const pathNames = [...requestMapping.pathParams, ...Object.keys(boundPathParams)]
  const path = substitutePath(requestMapping.path, pathNames, pathArgs)
  if (path.error) return errorResult(path)

  const url = new URL(path.value.replace(/^\//, ""), options.applicationBase)
  if (
    url.origin !== location.origin ||
    !url.pathname.startsWith(options.applicationBase.pathname)
  ) {
    return errorResult({ error: "Refused a tool request outside the FastAPI application." })
  }
  for (const name of requestMapping.queryParams) {
    if (args[name] !== undefined && args[name] !== null) {
      const values = Array.isArray(args[name]) ? args[name] : [args[name]]
      for (const value of values) url.searchParams.append(name, String(value))
    }
  }

  const method = requestMapping.method
  if (!ALLOWED_METHODS.has(method)) {
    return errorResult({ error: `Unsupported HTTP method: ${method}` })
  }

  const headers = new Headers({ Accept: "application/json" })
  for (const [inputName, headerName] of Object.entries(requestMapping.headerParams ?? {})) {
    if (args[inputName] !== undefined && args[inputName] !== null) {
      headers.set(headerName, String(args[inputName]))
    }
  }
  const request = {
    method,
    headers,
    credentials: options.credentials === "same-origin" ? "same-origin" : "omit",
    signal,
  }
  const body = requestBody(requestMapping, args)
  if (body.present) {
    headers.set("Content-Type", "application/json")
    request.body = JSON.stringify(body.value)
  }

  const response = await fetch(url, request)
  const text = await response.text()
  const result = { status: response.status, body: parseJson(text) }
  return response.ok ? textResult(result) : errorResult(result)
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
  const text = typeof value === "string" ? value : safeStringify(value)
  return { content: [{ type: "text", text }] }
}

function errorResult(value) {
  return { ...textResult(value), isError: true }
}

function isToolResult(value) {
  return Boolean(value && typeof value === "object" && Array.isArray(value.content))
}

function safeStringify(value) {
  try {
    const text = JSON.stringify(value)
    return text === undefined ? String(value) : text
  } catch {
    return String(value)
  }
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error)
}
