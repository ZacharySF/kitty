--!strict
-- The kitty! Inbox block's state and view (host half), without Tern's runtime: the view
-- builders come in as `ui` (tern.ui), so tests/inbox_test.luau renders it with fakes.
--
-- The block holds no token and makes no request. It asks a Tern window for its data with
-- `cx:open(core.inbox_link(pane))`; the window half fetches kitty!'s recent titles with the
-- token in its keychain and sends them back as an action event (`act = "kitty-inbox"`, the
-- payload JSON in `value`), which `apply` reads. Nothing is saved with the pane.

local core = require("./core")

export type Item = { key: string, label: string, detail: string, url: string }

export type State = {
	--- loading | ok | pairing | error | no-window
	phase: string,
	message: string,
	code: string?,
	origin: string?,
	pages: { Item },
	tasks: { Item },
	sel: number,
	tries: number,
	--- Bumped by every refresh, so an older round of asking stops.
	round: number,
}

local inbox = {}

inbox.ACTION = "kitty-inbox"
inbox.TITLE = "kitty! Inbox"
inbox.MAX_TRIES = 4

function inbox.new(): State
	return { phase = "loading", message = "", code = nil, origin = nil, pages = {}, tasks = {}, sel = 1, tries = 0, round = 0 }
end

local function day(stamp: any): string
	return if type(stamp) == "string" then (string.match(stamp, "^%d%d%d%d%-%d%d%-%d%d") or "") else ""
end

local function text(value: any): string
	return if type(value) == "string" then value else ""
end

--- Read one payload from the window half: {state = "ok", origin, recent} or
--- {state = "pairing", code} or {state = "error", message}.
function inbox.apply(state: State, payload: any)
	if type(payload) ~= "table" then
		return
	end
	local loopback = core.parse_endpoint({ origin = payload.origin, pairing = "/api/ingress/pair" })
	if payload.state == "ok" and loopback and type(payload.recent) == "table" then
		local origin: string = payload.origin
		local recent = payload.recent
		local pages: { Item } = {}
		local listed = false
		for _, page in (if type(recent.pages) == "table" then recent.pages else {}) do
			if type(page) == "table" and type(page.number) == "number" then
				local is_inbox = page.number == recent.inbox
				listed = listed or is_inbox
				table.insert(pages, {
					key = "p" .. page.number,
					label = if text(page.title) ~= "" then text(page.title) else "Untitled",
					detail = if is_inbox then "Inbox · " .. day(page.updated_at) else day(page.updated_at),
					url = core.page_url(origin, page.number),
				})
			end
		end
		if type(recent.inbox) == "number" and not listed then
			table.insert(pages, 1, { key = "p" .. recent.inbox, label = "Inbox", detail = "Inbox", url = core.page_url(origin, recent.inbox) })
		end
		local tasks: { Item } = {}
		for _, task in (if type(recent.tasks) == "table" then recent.tasks else {}) do
			if type(task) == "table" and task.id ~= nil then
				table.insert(tasks, {
					key = "t" .. tostring(task.id),
					label = if text(task.title) ~= "" then text(task.title) else "Untitled task",
					detail = if text(task.due) ~= "" then "due " .. text(task.due) else "",
					url = core.calendar_url(origin),
				})
			end
		end
		state.phase, state.message, state.code, state.origin = "ok", "", nil, origin
		state.pages, state.tasks = pages, tasks
		state.sel = math.clamp(state.sel, 1, math.max(1, #pages + #tasks))
	elseif payload.state == "pairing" and type(payload.code) == "string" then
		state.phase, state.code = "pairing", payload.code
		state.message = "Approve this Tern in kitty!: code " .. payload.code
	elseif payload.state == "error" then
		state.phase = "error"
		state.message = if text(payload.message) ~= "" then text(payload.message) else "kitty! could not be reached"
	end
end

--- Every listed item in order: pages, then tasks.
function inbox.items(state: State): { Item }
	local all = table.clone(state.pages)
	table.move(state.tasks, 1, #state.tasks, #all + 1, all)
	return all
end

function inbox.title(state: State): string
	return if state.phase == "ok" then string.format("%s (%d)", inbox.TITLE, #state.tasks) else inbox.TITLE
end

local function clickable(node: any, act: string): any
	local p: { [string]: any } = node.p or {}
	p.actions = { click = act }
	node.p = p
	return node
end

local function keyed(node: any, key: string): any
	local p: { [string]: any } = node.p or {}
	p.key = key
	node.p = p
	return node
end

local function listing(ui: any, section: string, items: { Item }, offset: number, sel: number, empty: string): any
	local rows = {}
	for i, item in items do
		rows[i] = { label = item.label, detail = if item.detail ~= "" then item.detail else nil }
	end
	local node = ui.list(rows)
	local children = node.c or {}
	for i, child in children do
		keyed(clickable(child, "open=" .. items[i].key), items[i].key)
	end
	local p: { [string]: any } = node.p or {}
	p.key = "items"
	p.empty = { ui.span(empty, "muted") }
	local current = sel - offset
	if current >= 1 and current <= #items then
		p.selected = string.format("main.%s.items.%s", section, items[current].key)
	end
	node.p = p
	return node
end

function inbox.view(state: State, ui: any): any
	local main = {}
	local head = { ui.span("kitty! Inbox", "strong") }
	if state.phase == "loading" then
		table.insert(head, ui.span("  asking kitty!…", "muted"))
	end
	table.insert(main, keyed(ui.text(head), "head"))
	if state.phase == "pairing" then
		table.insert(main, keyed(ui.card({ ui.span("Pair with kitty!", "strong") }, {
			ui.text({ ui.span("Code "), ui.span(state.code or "", "key") }),
			ui.text({ ui.span("Approve it in kitty! (the bar at the top, or Settings → Connected apps).", "muted") }),
		}), "pairing"))
	elseif state.phase == "error" then
		table.insert(main, keyed(ui.text({ ui.span(state.message, "error") }), "message"))
	elseif state.phase == "no-window" then
		table.insert(main, keyed(ui.text({
			ui.span("The Inbox fills from a Tern window with the kitty! plugin loaded; open one, then press r.", "muted"),
		}), "message"))
	end
	if state.phase == "ok" then
		table.insert(main, keyed(ui.section({ ui.span("Recent pages") }, {
			listing(ui, "pages", state.pages, 0, state.sel, "No pages yet"),
		}), "pages"))
		table.insert(main, keyed(ui.section({ ui.span("Open tasks") }, {
			listing(ui, "tasks", state.tasks, #state.pages, state.sel, "No open tasks"),
		}), "tasks"))
	end
	local dock = ui.row({
		clickable(ui.badge("Refresh", "accent"), "refresh"),
		clickable(ui.badge("Open kitty!", "muted"), "open-kitty"),
		ui.text({ ui.span("r refresh · j/k move · ↵ open", "muted") }),
	})
	return { main = ui.col(main), dock = ui.col({ dock }) }
end

export type Effect = { refresh: boolean?, open: string? }

local function open_key(state: State, key: string): Effect?
	for _, item in inbox.items(state) do
		if item.key == key then
			return { open = item.url }
		end
	end
	return nil
end

--- What a click asks for: a refresh or a URL to open.
function inbox.event(state: State, ev: any): Effect?
	if type(ev) ~= "table" or ev.ev ~= "action" then
		return nil
	end
	if ev.act == "refresh" then
		return { refresh = true }
	elseif ev.act == "open-kitty" and state.origin then
		return { open = state.origin .. "/" }
	elseif ev.act == "open" and type(ev.value) == "string" then
		return open_key(state, ev.value)
	end
	return nil
end

--- A key press: whether the view changed, and what it asks for.
function inbox.key(state: State, key: any): (boolean, Effect?)
	local count = #state.pages + #state.tasks
	local name = if type(key) == "table" then key.name else nil
	if name == "r" then
		return true, { refresh = true }
	elseif (name == "j" or name == "down") and count > 0 then
		state.sel = math.min(state.sel + 1, count)
		return true, nil
	elseif (name == "k" or name == "up") and count > 0 then
		state.sel = math.max(state.sel - 1, 1)
		return true, nil
	elseif name == "enter" and count > 0 then
		local item = inbox.items(state)[state.sel]
		return false, if item then { open = item.url } else nil
	end
	return false, nil
end

return inbox
