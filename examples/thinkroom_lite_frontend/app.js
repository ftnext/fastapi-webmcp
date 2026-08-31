import { registerWebMCP } from "/_webmcp/runtime.js"

const query = new URLSearchParams(location.search)
const slug = query.get("slug") ?? "demo"
const mode = query.get("mode") === "edit" ? "edit" : "view"
const editor = document.querySelector("#editor")
const saveButton = document.querySelector("#save")
const status = document.querySelector("#status")
const comments = document.querySelector("#comments")

const documentResponse = await fetch(`/api/documents/${encodeURIComponent(slug)}`)
if (!documentResponse.ok) throw new Error(`Could not load document (${documentResponse.status})`)
const roomDocument = await documentResponse.json()
editor.value = roomDocument.content
editor.readOnly = mode !== "edit"
saveButton.hidden = mode !== "edit"
renderComments(roomDocument.comments)

async function saveContent(content, signal) {
  const response = await fetch(`/api/documents/${encodeURIComponent(slug)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
    credentials: "omit",
    signal,
  })
  if (!response.ok) throw new Error(`Could not save document (${response.status})`)
  return await response.json()
}

saveButton.addEventListener("click", async () => {
  await saveContent(editor.value)
  status.textContent = "Saved by the page UI."
})

const registration = registerWebMCP({
  manifestUrl: `/_webmcp/manifest.json?slug=${encodeURIComponent(slug)}&mode=${mode}`,
  handlers: {
    replace_content: async ({ agent_name: agentName, content }, { signal, context }) => {
      if (!context.canWrite || mode !== "edit") throw new Error("This page is not writable.")
      if (typeof agentName !== "string" || agentName.trim() === "") {
        throw new Error("agent_name is required")
      }
      if (typeof content !== "string" || content.trim() === "") {
        throw new Error("content is required")
      }

      const previousContent = editor.value
      editor.value = content
      editor.dispatchEvent(new Event("input", { bubbles: true }))
      try {
        await saveContent(content, signal)
      } catch (error) {
        editor.value = previousContent
        throw error
      }
      return {
        ok: true,
        agent_name: agentName.trim(),
        previous_content: previousContent,
        content,
      }
    },
  },
})
window.addEventListener("pagehide", () => registration.abort(), { once: true })

const registered = await registration.ready
status.textContent = registered.supported
  ? `${registered.tools.length} WebMCP tools registered in ${mode} mode.`
  : `WebMCP unavailable; the ${mode} page still works normally.`

function renderComments(items) {
  comments.replaceChildren(
    ...items.map((comment) => {
      const item = document.createElement("li")
      item.textContent = `${comment.agent_name}: ${comment.body}`
      return item
    }),
  )
}
