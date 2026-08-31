import { registerWebMCP } from "./runtime.js"

const statusElement = document.querySelector("#webmcp-status")
const toolsElement = document.querySelector("#webmcp-tools")
const manifestElement = document.querySelector('meta[name="fastapi-webmcp-manifest"]')

const registration = registerWebMCP({
  manifestUrl: manifestElement?.content ?? "./manifest.json",
})
window.addEventListener("pagehide", () => registration.abort(), { once: true })

try {
  const result = await registration.ready
  if (!result.supported) {
    statusElement.textContent =
      "WebMCP is not available in this browser. The regular FastAPI application still works."
    toolsElement.replaceChildren(listItem("No browser tools registered."))
  } else {
    statusElement.textContent = `${result.tools.length} WebMCP tools registered.`
    toolsElement.replaceChildren(...result.tools.map((tool) => listItem(tool.name, true)))
  }
} catch (error) {
  statusElement.textContent = `WebMCP registration failed: ${errorMessage(error)}`
  toolsElement.replaceChildren(listItem("Registration failed."))
  console.error(error)
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
