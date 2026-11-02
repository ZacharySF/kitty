--!strict
-- Window half: palette commands that send things to kitty!, pairing, and the data behind
-- the host half's Inbox block. Every request to kitty! goes from here (lib/client.luau),
-- because only window halves have the keychain that holds kitty!'s token.

local core = require("./lib/core")
local Client = require("./lib/client")
local inbox = require("./lib/inbox")

local GROUP = "kitty!"
local PANE_LINES = 2000
local FAILED_LINES = 200

local client = Client.new({
	fetch = tern.fetch,
	json = tern.json,
	kv = tern.kv,
	secrets = (tern :: any).secrets,
	read_file = tern.fs.read,
	timer = tern.timer,
	now = tern.now,
	plugin_dir = tern.plugin.dir,
})

-- Inbox blocks waiting for data, by pane: told the pairing code while pairing runs.
local waiting: { [number]: boolean } = {}
-- The newest failed command line in this window and its pane's text when it failed.
local failed: { pane: number, line: string, status: number, text: string, cwd: string? }? = nil

local function push(cx: WindowCx, pane: number, payload: { [string]: any })
	cx.session:event(pane, { ev = "action", id = "main", act = inbox.ACTION, value = tern.json.encode(payload) })
end

client.on_pairing = function(cx: WindowCx, code: string, seconds: number)
	local minutes = math.max(1, math.ceil(seconds / 60))
	cx:toast("info", "kitty! pairing code " .. code,
		string.format("Approve it in kitty! within %d min: the bar at the top, or Settings → Connected apps.", minutes))
	for pane in waiting do
		push(cx, pane, { state = "pairing", code = code })
	end
end

client.on_paired = function(cx: WindowCx)
	cx:toast("success", "Tern is paired with kitty!")
end

client.on_notice = function(cx: WindowCx, level: string, text: string, sub: string?)
	cx:toast(level :: any, text, sub)
end

--- Toast the outcome of a send: `success` on ok, kitty!'s reason otherwise.
local function report(success: string, detail: ((data: any) -> string?)?)
	return function(result: core.Outcome, cx: WindowCx)
		if result.kind == "ok" then
			cx:toast("success", success, if detail then detail(result.data) else nil)
		else
			cx:toast("error", "kitty!: " .. result.message)
		end
	end
end

local function inbox_page(data: any): string?
	return if type(data) == "table" and type(data.page) == "number" then "Inbox page " .. data.page else nil
end

local function send_inbox(cx: WindowCx, blocks: { core.Block }, success: string)
	client:request(cx, "POST", "/api/ingress/inbox", { blocks = blocks }, report(success, inbox_page))
end

--- The text a pane shows, newest last, as plain text.
local function pane_text(cx: WindowCx, pane: number, lines: number): string
	local read = cx.session:read(pane, { as = "text", lines = lines, scrollback = true, max_chars = 64000 })
	return core.pane_text(read)
end

-- The Inbox block's data -----------------------------------------------------------

local function fill_inbox(cx: WindowCx, pane: number)
	waiting[pane] = true
	client:request(cx, "GET", "/api/ingress/recent?limit=20", nil, function(result, later)
		waiting[pane] = nil
		local endpoint = client:endpoint()
		if result.kind == "ok" and endpoint then
			push(later, pane, { state = "ok", origin = endpoint.origin, recent = result.data })
		else
			push(later, pane, { state = "error", message = result.message })
		end
	end)
end

tern.route.link(function(link, cx)
	local action, pane = core.parse_link(link.url)
	if action == nil then
		return nil
	end
	if action == "inbox" and pane then
		fill_inbox(cx, pane)
	end
	return { handled = true }
end)

-- Panes ---------------------------------------------------------------------------

tern.command({
	id = "capture-selection",
	title = "Capture selection to kitty! Inbox",
	group = GROUP,
	run = function(cx)
		local selection = cx.session:selection(nil, { max_chars = core.MAX_BLOCK_TEXT })
		local info = selection and cx.session:pane(selection.pane)
		local blocks = selection and core.selection_blocks(selection.text, info and info.title, info and info.cwd)
		if not blocks then
			cx:toast("info", "Select some text in a pane first")
			return
		end
		send_inbox(cx, blocks, "Selection saved to kitty!")
	end,
})

tern.command({
	id = "capture-pane",
	title = "Capture pane to kitty! Inbox",
	group = GROUP,
	run = function(cx)
		local pane = cx.session:focused()
		local info = pane and cx.session:pane(pane)
		if not pane or not info then
			cx:toast("info", "Focus the pane to capture first")
			return
		end
		if info.kind == "browser" then
			cx:toast("info", "This is a web page", "Use “Send browser page to kitty! Inbox” instead")
			return
		end
		local blocks = core.pane_blocks(pane_text(cx, pane, PANE_LINES), info.title, info.cwd)
		if not blocks then
			cx:toast("info", "This pane shows no text to capture")
			return
		end
		send_inbox(cx, blocks, "Pane saved to kitty!")
	end,
})

tern.command({
	id = "quick-note",
	title = "New kitty! quick note",
	group = GROUP,
	run = function(cx)
		cx:prompt({ placeholder = "New kitty! quick note", action = "save" }):next(function(text, _err, later)
			local body = core.note_body(text)
			if body and later then
				client:request(later, "POST", "/api/ingress/notes", body, report("Quick note saved to kitty!"))
			end
		end)
	end,
})

-- Failed commands ---------------------------------------------------------------------

tern.on("command_finished", function(ev: CommandFinishedEvent, cx: WindowCx)
	if not core.is_failure(ev.status) then
		return
	end
	local info = cx.session:pane(ev.pane)
	failed = {
		pane = ev.pane,
		line = ev.line,
		status = ev.status,
		text = pane_text(cx, ev.pane, FAILED_LINES),
		cwd = info and info.cwd,
	}
	if tern.kv.get("offer_failed") == true then
		cx:toast("info", string.format("Command failed (exit %d)", ev.status),
			"Palette: “Save failed command output to kitty!” keeps its output in kitty!'s Inbox")
	end
end)

tern.command({
	id = "save-failed",
	title = "Save failed command output to kitty!",
	group = GROUP,
	available = function(_cx)
		return failed ~= nil
	end,
	run = function(cx)
		local last = failed
		if not last then
			cx:toast("info", "No command has failed in this window yet")
			return
		end
		send_inbox(cx, core.failed_blocks(last.line, last.status, last.text, last.cwd), "Failed command saved to kitty!")
	end,
})

tern.command({
	id = "offer-failed",
	title = "Toggle kitty! offers for failed commands",
	group = GROUP,
	run = function(cx)
		local on = tern.kv.get("offer_failed") ~= true
		tern.kv.set("offer_failed", on)
		cx:toast("info", if on then "kitty! will offer to save failed commands" else "kitty! offers for failed commands are off")
	end,
})

-- Browser blocks ----------------------------------------------------------------------

-- The browser block to send: the focused one, the only one, or the one the user picks.
local function with_browser(cx: WindowCx, k: (pane: number, info: PaneInfo, cx: WindowCx) -> ())
	local focused = cx.session:focused()
	local info = focused and cx.session:pane(focused)
	if focused and info and info.kind == "browser" then
		k(focused, info, cx)
		return
	end
	local found: { PaneInfo } = {}
	for _, pane in cx.session:panes() do
		if pane.kind == "browser" then
			table.insert(found, pane)
		end
	end
	if #found == 0 then
		cx:toast("info", "Open a web page in a Tern browser block first")
	elseif #found == 1 then
		k(found[1].pane, found[1], cx)
	else
		local items = {}
		for i, pane in found do
			items[i] = { label = if pane.title ~= "" then pane.title else (pane.url or "Web page"), detail = pane.url, pane = pane.pane }
		end
		cx:pick({ items = items, placeholder = "Which page to send to kitty!?", action = "send" }):next(function(item, _err, later)
			if item and later then
				local chosen = later.session:pane(item.pane)
				if chosen then
					k(item.pane, chosen, later)
				end
			end
		end)
	end
end

local function mirror(cx: WindowCx, url: any, title: any, text: any, selection: any, success: string)
	local body, problem = core.mirror_body(url, title, text, selection)
	if not body then
		cx:toast("info", problem or "This page cannot be sent to kitty!")
		return
	end
	client:request(cx, "POST", "/api/ingress/mirror", body, report(success, inbox_page))
end

tern.command({
	id = "send-page",
	title = "Send browser page to kitty! Inbox",
	group = GROUP,
	run = function(cx)
		with_browser(cx, function(pane, info, now)
			local selected = now.session:selection(pane, { max_chars = core.MAX_BLOCK_TEXT })
			local selection = if selected then selected.text else nil
			now.browser:call({ op = "snapshot", block = pane, mode = "text", format = "text" }):next(function(result, err, later)
				if not later then
					return
				end
				local ok = if result and type(result.ok) == "table" then result.ok else nil
				if not ok then
					local reason = if result and result.error then result.error.message else err
					later:toast("error", "Tern could not read the page", reason)
					return
				end
				local text = if type(ok.text) == "string" then ok.text elseif type(ok.tree) == "string" then ok.tree else nil
				mirror(later, ok.url or info.url, ok.title or info.title, text, selection, "Page sent to kitty!")
			end)
		end)
	end,
})

tern.command({
	id = "pick-page",
	title = "Pick from browser page to kitty! Inbox",
	group = GROUP,
	run = function(cx)
		with_browser(cx, function(pane, info, now)
			now:toast("info", "Click what to send to kitty!", "Escape cancels")
			now.browser:call({ op = "pick", block = pane }):next(function(result, err, later)
				if not later then
					return
				end
				local ok = if result and type(result.ok) == "table" then result.ok else nil
				if not ok then
					local reason = if result and result.error then result.error.message else err
					later:toast("error", "Tern could not pick from the page", reason)
					return
				end
				if ok.cancelled or type(ok.picked) ~= "table" then
					return
				end
				local picked = ok.picked
				local text = picked.text or picked.value or picked.name
				mirror(later, ok.url or info.url, ok.title or info.title, nil, text, "Picked text sent to kitty!")
			end)
		end)
	end,
})

-- Connection ---------------------------------------------------------------------------

tern.command({
	id = "connect",
	title = "Connect to kitty!",
	group = GROUP,
	run = function(cx)
		client:request(cx, "GET", "/api/ingress/client", nil, report("Tern is connected to kitty!", function(data)
			return if type(data) == "table" and type(data.scopes) == "table"
				then "Allowed: " .. table.concat(data.scopes, ", ")
				else nil
		end))
	end,
})
