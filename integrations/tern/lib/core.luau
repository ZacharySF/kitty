--!strict
-- Pure logic of the kitty! plugin, shared by both halves and unit-tested without Tern
-- (tests/core_test.luau). Nothing here touches the `tern` API.

local core = {}

core.KIND = "tern-plugin"
core.SCOPES = { "capture", "read" }
core.CLIENT_NAME = "Tern"
--- Links between the halves: the Inbox block asks the window half for data with
--- `cx:open("kitty-tern://inbox?pane=N")`, which the window's `tern.route.link` claims.
core.LINK = "kitty-tern://"
--- kitty!'s ingress limits (src/kitty/core/ingress.py, quick_notes.py), counted here in
--- bytes, which is never fewer than kitty!'s characters.
core.MAX_BLOCK_TEXT = 20000
core.MAX_NOTE_TEXT = 64 * 1024
core.MAX_PAGE_TEXT = 200000
core.MAX_TITLE = 300
core.MAX_URL = 2048
core.TRIMMED = "[earlier output trimmed]"

export type Endpoint = { origin: string, pairing: string }

export type Outcome = {
	--- ok | offline | unauthorized | disabled | forbidden | busy | failed
	kind: string,
	status: number,
	data: any,
	message: string,
}

local function trim(text: string): string
	return (string.gsub(text, "^%s+", ""):gsub("%s+$", ""))
end
core.trim = trim

--- `<workspace>/.private` for the copy kitty! installs at `<workspace>/.private/tern/plugin`,
--- or nil for a package that kitty! did not install (a developer's own link).
function core.private_dir(plugin_dir: string): string?
	local dir = string.gsub(plugin_dir, "[/\\]+$", "")
	return string.match(dir, "^(.+[/\\]%.private)[/\\]tern[/\\]plugin$")
end

function core.endpoint_path(plugin_dir: string): string?
	local private = core.private_dir(plugin_dir)
	return if private then private .. "/endpoint.json" else nil
end

--- The published endpoint (kitty/core/endpoint.py), accepted only on a loopback origin so a
--- rewritten file cannot send the token anywhere else.
function core.parse_endpoint(data: any): (Endpoint?, string?)
	if type(data) ~= "table" or type(data.origin) ~= "string" or type(data.pairing) ~= "string" then
		return nil, "kitty!'s endpoint file is not readable"
	end
	local origin: string, pairing: string = data.origin, data.pairing
	local port = string.match(origin, "^http://127%.0%.0%.1:(%d+)$") or string.match(origin, "^http://%[::1%]:(%d+)$")
	local number = port and tonumber(port)
	if not number or number < 1 or number > 65535 then
		return nil, "kitty!'s endpoint file names an address that is not this computer"
	end
	if pairing ~= "/api/ingress/pair" then
		return nil, "kitty!'s endpoint file is from an unknown kitty! version"
	end
	local endpoint: Endpoint = { origin = origin, pairing = pairing :: string }
	return endpoint, nil
end

--- What one HTTP exchange with kitty! came to. `decoded` is the JSON body when it parsed.
function core.classify(status: number, err: string?, decoded: any): Outcome
	if err then
		return {
			kind = "offline",
			status = 0,
			data = nil,
			message = "kitty! isn't running or can't be reached. Start kitty!, then try again.",
		}
	end
	local server = if type(decoded) == "table" and type(decoded.error) == "string" then decoded.error else nil
	if status >= 200 and status < 300 then
		return { kind = "ok", status = status, data = decoded, message = "" }
	elseif status == 401 then
		return { kind = "unauthorized", status = status, data = nil, message = "kitty! no longer accepts this Tern's token" }
	elseif status == 403 and server and string.find(server, "turned off", 1, true) then
		return { kind = "disabled", status = status, data = nil, message = server }
	elseif status == 403 then
		return { kind = "forbidden", status = status, data = nil, message = server or "kitty! refused the request" }
	elseif status == 429 then
		return { kind = "busy", status = status, data = nil, message = server or "kitty! is busy; try again later" }
	end
	return {
		kind = "failed",
		status = status,
		data = nil,
		message = server or ("kitty! answered HTTP " .. tostring(status)),
	}
end

--- The first `limit` bytes of `text`, cut on a UTF-8 character boundary.
function core.head(text: string, limit: number): string
	if #text <= limit then
		return text
	end
	local stop = limit
	while stop > 0 do
		local byte = string.byte(text, stop + 1)
		if byte == nil or byte < 0x80 or byte >= 0xC0 then
			break
		end
		stop -= 1
	end
	return string.sub(text, 1, stop)
end

--- The last `limit` bytes of `text` (newest output), cut on a UTF-8 boundary and marked.
function core.tail(text: string, limit: number): (string, boolean)
	if #text <= limit then
		return text, false
	end
	local room = limit - #core.TRIMMED - 1
	local start = #text - room + 1
	while start <= #text do
		local byte = string.byte(text, start)
		if byte < 0x80 or byte >= 0xC0 then
			break
		end
		start += 1
	end
	return core.TRIMMED .. "\n" .. string.sub(text, start), true
end

--- Pane text as `cx.session:read(..., {as = "text"})` gives it, without Tern's
--- "[… N earlier lines; increase lines]" header and trailing blank lines.
function core.pane_text(read: any): string
	if type(read) ~= "string" then
		return ""
	end
	local text = string.gsub(read, "^%[[^\n]-earlier lines[^\n]-%]\n", "")
	text = string.gsub(text, "[%s]+$", "")
	return text
end

local function source_line(prefix: string, title: string?, cwd: string?): string
	local parts = { prefix }
	if title and trim(title) ~= "" then
		table.insert(parts, trim(title))
	end
	if cwd and cwd ~= "" then
		table.insert(parts, cwd)
	end
	return core.head(table.concat(parts, " · "), core.MAX_BLOCK_TEXT)
end

export type Block = { type: string, text: string }

--- Inbox blocks for a selection: the text as a quote, then where it came from.
function core.selection_blocks(text: string, title: string?, cwd: string?): { Block }?
	if trim(text) == "" then
		return nil
	end
	return {
		{ type = "quote", text = core.head(text, core.MAX_BLOCK_TEXT) },
		{ type = "text", text = source_line("From Tern", title, cwd) },
	}
end

--- Inbox blocks for a pane's output: where it came from, then the newest text as code.
function core.pane_blocks(text: string, title: string?, cwd: string?): { Block }?
	if trim(text) == "" then
		return nil
	end
	local body = core.tail(text, core.MAX_BLOCK_TEXT)
	return {
		{ type = "text", text = source_line("Tern pane", title, cwd) },
		{ type = "code", text = body },
	}
end

--- Inbox blocks for a failed command: the line and its status, then its output.
function core.failed_blocks(line: string, status: number, text: string, cwd: string?): { Block }
	local head = string.format("Failed in Tern (exit %d): %s", status, trim(line))
	local blocks: { Block } = { { type = "text", text = source_line(head, nil, cwd) } }
	if trim(text) ~= "" then
		table.insert(blocks, { type = "code", text = (core.tail(text, core.MAX_BLOCK_TEXT)) })
	end
	return blocks
end

--- The body of a quick note, or nil for blank text.
function core.note_body(text: string?): { text: string }?
	if text == nil or trim(text) == "" then
		return nil
	end
	return { text = core.head(text, core.MAX_NOTE_TEXT) }
end

--- The `/api/ingress/mirror` body for a web page, or nil with the reason it cannot be sent.
function core.mirror_body(url: any, title: any, text: any, selection: any): ({ [string]: string }?, string?)
	if type(url) ~= "string" or not (string.match(url, "^https?://[^%s]+$")) or #url > core.MAX_URL then
		return nil, "Only http and https pages can be sent to kitty!"
	end
	local name = if type(title) == "string" then trim((string.gsub(title, "%s+", " "))) else ""
	if name == "" then
		name = url
	end
	local body: { [string]: string } = { url = url, title = core.head(name, core.MAX_TITLE) }
	if type(text) == "string" and trim(text) ~= "" then
		body.text = core.head(text, core.MAX_PAGE_TEXT)
	end
	if type(selection) == "string" and trim(selection) ~= "" then
		body.selection = core.head(selection, core.MAX_BLOCK_TEXT)
	end
	return body, nil
end

--- kitty! opens a note page at `#notes/<number>` and tasks in its Calendar.
function core.page_url(origin: string, number: number): string
	return string.format("%s/#notes/%d", origin, number)
end

function core.calendar_url(origin: string): string
	return origin .. "/#calendar"
end

--- A `kitty-tern://` link: its action and pane, or nil for any other link.
function core.parse_link(url: string): (string?, number?)
	if string.sub(url, 1, #core.LINK) ~= core.LINK then
		return nil, nil
	end
	local action = string.match(url, "^kitty%-tern://([%a%-]+)")
	local pane = string.match(url, "[?&]pane=(%d+)")
	return action, if pane then tonumber(pane) else nil
end

function core.inbox_link(pane: number): string
	return string.format("%sinbox?pane=%d", core.LINK, pane)
end

--- Whether a finished command is worth offering to save: a failure that is not the user
--- interrupting it (130 is Ctrl+C, 141 a closed pipe).
function core.is_failure(status: any): boolean
	return type(status) == "number" and status ~= 0 and status ~= 130 and status ~= 141
end

return core
